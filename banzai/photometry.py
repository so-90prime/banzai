import logging
from urllib.parse import urljoin

import numpy as np
from astropy.table import Table
import sep
from requests import HTTPError

from banzai.utils import stats, array_utils
from banzai.utils.photometry_utils import get_reference_sources, match_catalogs, to_magnitude, fit_photometry
from banzai.stages import Stage
from banzai.data import DataTable
from banzai import logs

from skimage import measure

logger = logging.getLogger('banzai')
sep.set_sub_object_limit(int(1e6))


def radius_of_contour(contour, source):
    x = contour[:, 1]
    y = contour[:, 0]
    x_center = (source['xmax'] - source['xmin'] + 1) / 2.0 - 0.5
    y_center = (source['ymax'] - source['ymin'] + 1) / 2.0 - 0.5

    return np.percentile(np.sqrt((x - x_center)**2.0 + (y - y_center)** 2.0), 90)


class SourceDetector(Stage):
    # Note that threshold is number of sigma, not an absolute number because we provide the error
    # array to SEP.
    threshold = 10.0
    min_area = 9

    def __init__(self, runtime_context):
        super(SourceDetector, self).__init__(runtime_context)

    def do_stage(self, image):
        try:
            # Increase the internal buffer size in sep. This is most necessary for crowded fields.
            ny, nx = image.shape
            sep.set_extract_pixstack(int(nx * ny - 1))

            data = image.data.copy()
            error = image.uncertainty
            mask = image.mask > 0

            # Fits can be backwards byte order, so fix that if need be and subtract
            # the background
            try:
                bkg = sep.Background(data, mask=mask, bw=32, bh=32, fw=3, fh=3)
            except ValueError:
                data = data.byteswap(True).newbyteorder()
                bkg = sep.Background(data, mask=mask, bw=32, bh=32, fw=3, fh=3)
            bkg.subfrom(data)

            # Do an initial source detection
            try:
                sources = sep.extract(data, self.threshold, mask=mask, minarea=self.min_area,
                                      err=error, deblend_cont=0.005)
            except Exception:
                logger.error(logs.format_exception(), image=image)
                return image

            # Convert the detections into a table
            sources = Table(sources)

            # We remove anything with a detection flag >= 8
            # This includes memory overflows and objects that are too close the edge
            sources = sources[sources['flag'] < 8]

            sources = array_utils.prune_nans_from_table(sources)

            # Calculate the ellipticity
            sources['ellipticity'] = 1.0 - (sources['b'] / sources['a'])

            # Fix any value of theta that are invalid due to floating point rounding
            # -pi / 2 < theta < pi / 2
            sources['theta'][sources['theta'] > (np.pi / 2.0)] -= np.pi
            sources['theta'][sources['theta'] < (-np.pi / 2.0)] += np.pi

            # Calculate the kron radius
            kronrad, krflag = sep.kron_radius(data, sources['x'], sources['y'],
                                              sources['a'], sources['b'],
                                              sources['theta'], 6.0)
            sources['flag'] |= krflag
            sources['kronrad'] = kronrad

            # Calcuate the equivilent of flux_auto
            flux, fluxerr, flag = sep.sum_ellipse(data, sources['x'], sources['y'],
                                                  sources['a'], sources['b'],
                                                  np.pi / 2.0, 2.5 * kronrad,
                                                  subpix=1, err=error)
            sources['flux'] = flux
            sources['fluxerr'] = fluxerr
            sources['flag'] |= flag

            # Do circular aperture photometry for diameters of 1" to 6"
            for diameter in [1, 2, 3, 4, 5, 6]:
                flux, fluxerr, flag = sep.sum_circle(data, sources['x'], sources['y'],
                                                     diameter / 2.0 / image.pixel_scale, gain=1.0, err=error)
                sources['fluxaper{0}'.format(diameter)] = flux
                sources['fluxerr{0}'.format(diameter)] = fluxerr
                sources['flag'] |= flag

            # Measure the flux profile
            flux_radii, flag = sep.flux_radius(data, sources['x'], sources['y'],
                                               6.0 * sources['a'], [0.25, 0.5, 0.75],
                                               normflux=sources['flux'], subpix=5)
            sources['flag'] |= flag
            sources['fluxrad25'] = flux_radii[:, 0]
            sources['fluxrad50'] = flux_radii[:, 1]
            sources['fluxrad75'] = flux_radii[:, 2]

            # Cut individual bright pixels. Often cosmic rays
            sources = sources[sources['fluxrad50'] > 0.5]

            # Calculate the FWHMs of the stars:
            sources['fwhm'] = np.nan
            sources['fwtm'] = np.nan
            # Here we estimate contours
            for source in sources:
                if source['flag'] == 0:
                    for ratio, keyword in zip([0.5, 0.1], ['fwhm', 'fwtm']):
                        contours = measure.find_contours(data[source['ymin']: source['ymax'] + 1,
                                                         source['xmin']: source['xmax'] + 1],
                                                         ratio * source['peak'])
                        if contours:
                            # If there are multiple contours like a donut might have take the outer
                            contour_radii = [radius_of_contour(contour, source) for contour in contours]
                            source[keyword] = 2.0 * np.nanmax(contour_radii)

            # Calculate the windowed positions
            sig = 2.0 / 2.35 * sources['fwhm']
            xwin, ywin, flag = sep.winpos(data, sources['x'], sources['y'], sig)
            sources['flag'] |= flag
            sources['xwin'] = xwin
            sources['ywin'] = ywin

            # Calculate the average background at each source
            bkgflux, fluxerr, flag = sep.sum_ellipse(bkg.back(), sources['x'], sources['y'],
                                                     sources['a'], sources['b'], np.pi / 2.0,
                                                     2.5 * sources['kronrad'], subpix=1)
            # masksum, fluxerr, flag = sep.sum_ellipse(mask, sources['x'], sources['y'],
            #                                         sources['a'], sources['b'], np.pi / 2.0,
            #                                         2.5 * kronrad, subpix=1)

            background_area = (2.5 * sources['kronrad']) ** 2.0 * sources['a'] * sources['b'] * np.pi # - masksum
            sources['background'] = bkgflux
            sources['background'][background_area > 0] /= background_area[background_area > 0]
            # Update the catalog to match fits convention instead of python array convention
            sources['x'] += 1.0
            sources['y'] += 1.0

            sources['xpeak'] += 1
            sources['ypeak'] += 1

            sources['xwin'] += 1.0
            sources['ywin'] += 1.0

            sources['theta'] = np.degrees(sources['theta'])

            catalog = sources['x', 'y', 'xwin', 'ywin', 'xpeak', 'ypeak',
                              'flux', 'fluxerr', 'peak', 'fluxaper1', 'fluxerr1',
                              'fluxaper2', 'fluxerr2', 'fluxaper3', 'fluxerr3',
                              'fluxaper4', 'fluxerr4', 'fluxaper5', 'fluxerr5',
                              'fluxaper6', 'fluxerr6', 'background', 'fwhm', 'fwtm',
                              'a', 'b', 'theta', 'kronrad', 'ellipticity',
                              'fluxrad25', 'fluxrad50', 'fluxrad75',
                              'x2', 'y2', 'xy', 'flag']

            # Add the units and description to the catalogs
            catalog['x'].unit = 'pixel'
            catalog['x'].description = 'X coordinate of the object'
            catalog['y'].unit = 'pixel'
            catalog['y'].description = 'Y coordinate of the object'
            catalog['xwin'].unit = 'pixel'
            catalog['xwin'].description = 'Windowed X coordinate of the object'
            catalog['ywin'].unit = 'pixel'
            catalog['ywin'].description = 'Windowed Y coordinate of the object'
            catalog['xpeak'].unit = 'pixel'
            catalog['xpeak'].description = 'X coordinate of the peak'
            catalog['ypeak'].unit = 'pixel'
            catalog['ypeak'].description = 'Windowed Y coordinate of the peak'
            catalog['flux'].unit = 'count'
            catalog['flux'].description = 'Flux within a Kron-like elliptical aperture'
            catalog['fluxerr'].unit = 'count'
            catalog['fluxerr'].description = 'Error on the flux within Kron aperture'
            catalog['peak'].unit = 'count'
            catalog['peak'].description = 'Peak flux (flux at xpeak, ypeak)'
            for diameter in [1, 2, 3, 4, 5, 6]:
                catalog['fluxaper{0}'.format(diameter)].unit = 'count'
                catalog['fluxaper{0}'.format(diameter)].description = 'Flux from fixed circular aperture: {0}" diameter'.format(diameter)
                catalog['fluxerr{0}'.format(diameter)].unit = 'count'
                catalog['fluxerr{0}'.format(diameter)].description = 'Error on Flux from circular aperture: {0}"'.format(diameter)

            catalog['background'].unit = 'count'
            catalog['background'].description = 'Average background value in the aperture'
            catalog['fwhm'].unit = 'pixel'
            catalog['fwhm'].description = 'FWHM of the object'
            catalog['fwtm'].unit = 'pixel'
            catalog['fwtm'].description = 'Full-Width Tenth Maximum'
            catalog['a'].unit = 'pixel'
            catalog['a'].description = 'Semi-major axis of the object'
            catalog['b'].unit = 'pixel'
            catalog['b'].description = 'Semi-minor axis of the object'
            catalog['theta'].unit = 'degree'
            catalog['theta'].description = 'Position angle of the object'
            catalog['kronrad'].unit = 'pixel'
            catalog['kronrad'].description = 'Kron radius used for extraction'
            catalog['ellipticity'].description = 'Ellipticity'
            catalog['fluxrad25'].unit = 'pixel'
            catalog['fluxrad25'].description = 'Radius containing 25% of the flux'
            catalog['fluxrad50'].unit = 'pixel'
            catalog['fluxrad50'].description = 'Radius containing 50% of the flux'
            catalog['fluxrad75'].unit = 'pixel'
            catalog['fluxrad75'].description = 'Radius containing 75% of the flux'
            catalog['x2'].unit = 'pixel^2'
            catalog['x2'].description = 'Variance on X coordinate of the object'
            catalog['y2'].unit = 'pixel^2'
            catalog['y2'].description = 'Variance on Y coordinate of the object'
            catalog['xy'].unit = 'pixel^2'
            catalog['xy'].description = 'XY covariance of the object'
            catalog['flag'].description = 'Bit mask of extraction/photometry flags'

            catalog.sort('flux')
            catalog.reverse()

            # Save some background statistics in the header
            mean_background = stats.sigma_clipped_mean(bkg.back(), 5.0)
            image.meta['L1MEAN'] = (mean_background,
                                    '[counts] Sigma clipped mean of frame background')

            median_background = np.median(bkg.back())
            image.meta['L1MEDIAN'] = (median_background,
                                      '[counts] Median of frame background')

            std_background = stats.robust_standard_deviation(bkg.back())
            image.meta['L1SIGMA'] = (std_background,
                                     '[counts] Robust std dev of frame background')

            # Save some image statistics to the header
            good_objects = catalog['flag'] == 0
            for quantity in ['fwhm', 'ellipticity', 'theta']:
                good_objects = np.logical_and(good_objects, np.logical_not(np.isnan(catalog[quantity])))
            if good_objects.sum() == 0:
                image.meta['L1FWHM'] = ('NaN', '[arcsec] Frame FWHM in arcsec')
                image.meta['L1FWTM'] = ('NaN', 'Ratio of FWHM to Full-Width Tenth Max')

                image.meta['L1ELLIP'] = ('NaN', 'Mean image ellipticity (1-B/A)')
                image.meta['L1ELLIPA'] = ('NaN', '[deg] PA of mean image ellipticity')
            else:
                seeing = np.nanmedian(catalog['fwhm'][good_objects]) * image.pixel_scale
                image.meta['L1FWHM'] = (seeing, '[arcsec] Frame FWHM in arcsec')
                image.meta['L1FWTM'] = (np.nanmedian(catalog['fwtm'][good_objects] / catalog['fwhm'][good_objects]),
                                        'Ratio of FWHM to Full-Width Tenth Max')

                mean_ellipticity = stats.sigma_clipped_mean(catalog['ellipticity'][good_objects], 3.0)
                image.meta['L1ELLIP'] = (mean_ellipticity, 'Mean image ellipticity (1-B/A)')

                mean_position_angle = stats.sigma_clipped_mean(catalog['theta'][good_objects], 3.0)
                image.meta['L1ELLIPA'] = (mean_position_angle,'[deg] PA of mean image ellipticity')

            logging_tags = {key: float(image.meta[key]) for key in ['L1MEAN', 'L1MEDIAN', 'L1SIGMA',
                                                                    'L1FWHM', 'L1ELLIP', 'L1ELLIPA']}

            logger.info('Extracted sources', image=image, extra_tags=logging_tags)
            # adding catalog (a data table) to the appropriate images attribute.
            image.add_or_update(DataTable(catalog, name='CAT'))
        except Exception:
            logger.error(logs.format_exception(), image=image)
        return image


class PhotometricCalibrator(Stage):
    color_to_fit = {'gp': 'g-r', 'rp': 'r-i', 'ip': 'r-i', 'zs': 'i-z'}

    def __init__(self, runtime_context):
        super(PhotometricCalibrator, self).__init__(runtime_context)

    def do_stage(self, image):
        if image.filter not in ['gp', 'rp', 'ip', 'zs']:
            return image

        if image['CAT'] is None:
            logger.warning("Not photometrically calibrating image because no catalog exists", image=image)
            return image

        if image.meta.get('WCSERR', 1) > 0:
            logger.warning("Not photometrically calibrating image because WCS solution failed", image=image)
            return image

        try:
            # Get the sources in the frame
            reference_catalog = get_reference_sources(image.meta,
                                                      urljoin(self.runtime_context.REFERENCE_CATALOG_URL, '/image'),
                                                      nx=image.shape[1], ny=image.shape[0])
        except HTTPError as e:
            logger.error(f'Error retrieving photometric reference catalog: {e}', image=image)
            return image

        # Match the catalog to the detected sources
        good_sources = np.logical_and(image['CAT'].data['flag'] == 0, image['CAT'].data['flux'] > 0.0)
        matched_catalog = match_catalogs(image['CAT'].data[good_sources], reference_catalog)

        if len(matched_catalog) == 0:
            logger.error('No matching sources found. Skipping zeropoint determination', image=image)
            return image
        # catalog_mag = instrumental_mag + zeropoint + color_coefficient * color
        # Fit the zeropoint and color_coefficient rejecting outliers
        # Note the zero index here in the filter name is because we only store teh first letter of the filter name
        try:
            zeropoint, zeropoint_error, color_coefficient, color_coefficient_error = \
                fit_photometry(matched_catalog, image.filter[0], self.color_to_fit[image.filter], image.exptime)
        except:
            logger.error(f"Error fitting photometry: {logs.format_exception()}", image=image)
            return image

        # Save the zeropoint, color coefficient and errors to header
        image.meta['L1ZP'] = zeropoint, "Instrumental zeropoint [mag]"
        image.meta['L1ZPERR'] = zeropoint_error, "Error on Instrumental zeropoint [mag]"
        image.meta['L1COLORU'] = self.color_to_fit[image.filter], "Color used for calibration"
        image.meta['L1COLOR'] = color_coefficient, "Color coefficient [mag]"
        image.meta['L1COLERR'] = color_coefficient_error, "Error on color coefficient [mag]"
        # Calculate the mag of each of the items in the catalog (without the color term) saving them
        image['CAT'].data['mag'], image['CAT'].data['magerr'] = to_magnitude(image['CAT'].data['flux'], image['CAT'].data['fluxerr'],
                                                                             zeropoint, image.exptime)
        return image
