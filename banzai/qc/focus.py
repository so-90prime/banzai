import logging

import numpy as np
import matplotlib.pyplot as plt
from astropy.convolution import interpolate_replace_nans, Box2DKernel

from banzai.stages import Stage
from banzai.utils import qc

logger = logging.getLogger('banzai')


def plot_value_vs_position(x, y, Z, ax=None):
    if ax is None:
        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')
    xgrid, ygrid = np.meshgrid(x, y)
    ax.plot_surface(xgrid, ygrid, Z, cmap=plt.cm.viridis, linewidth=0, antialiased=False)
    ax.set_xlabel('x')
    ax.set_ylabel('y')


def plot_ellipticity_vs_position(sources, ax=None):
    if ax is None:
        ax = plt.axes()
    ax.quiver(sources['x'], sources['y'], *sources['ellip_vector'].T)
    ax.set_aspect('equal')
    ax.autoscale(tight=True)
    ax.set_xlabel('x')
    ax.set_ylabel('y')
    ax.set_title('Ellipticity: left=vertical, right=horizontal')


def scatter_sources_vs_position(sources, col, image_shape, ax=None, bins=4):
    if ax is None:
        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')
    sources = sources[~np.isnan(sources[col])]
    ii = np.linspace(0, image_shape[0], bins + 1, dtype=int)
    jj = np.linspace(0, image_shape[1], bins + 1, dtype=int)
    for imin, imax in zip(ii[:-1], ii[1:]):
        for jmin, jmax in zip(jj[:-1], jj[1:]):
            chip = sources[(sources['x'] > jmin) & (sources['x'] < jmax) &
                           (sources['y'] > imin) & (sources['y'] < imax)]
            ax.scatter(chip['x'], chip['y'], chip[col], marker='.')
    z0, z1 = np.percentile(sources[col], (2.5, 97.5))
    ax.set_zlim(z0, z1)
    ax.set_xlabel('x')
    ax.set_ylabel('y')
    ax.set_zlabel(col)


def compute_source_gradient(sources, col, image_shape, bins=8):
    sources_good = sources[~np.isnan(sources[col])]
    xbins = np.linspace(0, image_shape[1], bins + 1, dtype=int)
    ybins = np.linspace(0, image_shape[0], bins + 1, dtype=int)
    Z = np.histogram2d(sources_good['x'], sources_good['y'], (xbins, ybins), weights=sources_good[col])[0] / \
        np.histogram2d(sources_good['x'], sources_good['y'], (xbins, ybins))[0]
    Z = interpolate_replace_nans(Z, Box2DKernel(2))
    xctrs = (xbins[:-1] + xbins[1:]) // 2
    yctrs = (ybins[:-1] + xbins[1:]) // 2
    xgrad, ygrad = np.gradient(Z, xctrs, yctrs)
    xgrad_mean = xgrad.mean()
    ygrad_mean = ygrad.mean()
    gradient = (xgrad_mean ** 2. + ygrad_mean ** 2.) ** 0.5
    gradient_angle = np.degrees(np.arctan2(ygrad_mean, xgrad_mean))
    return gradient, gradient_angle, xctrs, yctrs, Z


class FocusTest(Stage):
    """
    Calculate the FWHM and ellipticity as a function of position on the CCD, make a diagnostic plot, and log some stats
    """
    def __init__(self, runtime_context):
        super(FocusTest, self).__init__(runtime_context)

    def do_stage(self, image):
        sources = image['CAT'].data
        fwhm_grad, fwhm_grad_angle, xctrs, yctrs, Z = compute_source_gradient(sources, 'fwhm', image.shape)
        logger.info(f'FWHM gradient: {fwhm_grad:.6f} pix/pix at {fwhm_grad_angle:.0f}°', image=image)

        theta = 2. * np.radians(sources['theta'])  # factor of two is for 180 deg symmetry
        sources['ellip_vector'] = np.transpose(sources['ellipticity'] *[np.cos(theta), np.sin(theta)])
        ex, ey = sources['ellip_vector'].mean(axis=0)
        ellip_mean = (ex ** 2. + ey ** 2.) ** 0.5
        theta_mean = np.degrees(np.arctan2(ey, ex) / 2.)
        logger.info(f'Ellipticity vector: {ellip_mean:.4f} at {theta_mean:.0f}°', image=image)

        # make diagnostic plot for observers
        fig = plt.figure(figsize=(8., 4.))
        ax1 = fig.add_subplot(121, projection='3d')
        ax2 = fig.add_subplot(122)
        plot_value_vs_position(xctrs, yctrs, Z, ax=ax1)
        ax1.set_title('FWHM (pixels)')
        plot_ellipticity_vs_position(sources, ax=ax2)
        fig.tight_layout(pad=2.)
        fig.savefig('image_quality.pdf')
        plt.close(fig)

        qc_results = {'focus_test.fwhm': image.meta['L1FWHM'],
                      'focus_test.ellipticity': image.meta['L1ELLIP'],
                      'focus_test.ellipticity_angle': image.meta['L1ELLIPA'],
                      'focus_test.ellipticity_vector_magnitude': ellip_mean,
                      'focus_test.ellipticity_vector_angle': theta_mean,
                      'focus_test.fwhm_gradient_magnitude': fwhm_grad,
                      'focus_test.fwhm_gradient_angle': fwhm_grad_angle}
        qc.save_qc_results(self.runtime_context, qc_results, image)

        return image
