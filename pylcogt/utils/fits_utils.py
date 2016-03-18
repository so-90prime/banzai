from __future__ import absolute_import, print_function, division
from astropy.io import fits
import numpy as np
import os
import shutil
import tempfile
from . import date_utils

__author__ = 'cmccully'


def sanitizeheader(header):
    # Remove the mandatory keywords from a header so it can be copied to a new
    # image.
    header = header.copy()

    # Let the new data decide what these values should be
    for i in ['SIMPLE', 'BITPIX', 'BSCALE', 'BZERO']:
        if i in header.keys():
            header.pop(i)

    return header


def create_master_calibration_header(images):
    header = fits.Header()
    for h in images[0].header.keys():
        header[h] = images[0].header[h]

    header = sanitizeheader(header)

    observation_dates = [image.dateobs for image in images]
    mean_dateobs = date_utils.mean_date(observation_dates)

    header['DATE-OBS'] = date_utils.date_obs_to_string(mean_dateobs)

    header.add_history("Images combined to create master calibration image:")
    for image in images:
        header.add_history(image.filename)
    return header


def split_slice(pixel_section):
    pixels = pixel_section.split(':')
    if int(pixels[1]) > int(pixels[0]):
        pixel_slice = slice(int(pixels[0]) - 1, int(pixels[1]), 1)
    else:
        if int(pixels[1]) == 1:
            pixel_slice = slice(int(pixels[0]) - 1, None, -1)
        else:
            pixel_slice = slice(int(pixels[0]) - 1, int(pixels[1]) - 2, -1)
    return pixel_slice


def parse_region_keyword(keyword_value):
    """
    Convert a header keyword of the form [x1:x2],[y1:y2] into index slices
    :param keyword_value: Header keyword string
    :return: x, y index slices
    """

    if keyword_value.lower() == 'unknown':
        pixel_slices = None
    elif keyword_value.lower() == 'n/a':
        pixel_slices = None
    else:
        # Strip off the brackets and split the coordinates
        pixel_sections = keyword_value[1:-1].split(',')
        x_slice = split_slice(pixel_sections[0])
        y_slice = split_slice(pixel_sections[1])
        pixel_slices = (y_slice, x_slice)
    return pixel_slices


def fits_formats(format_code):
    """
    Convert a numpy data type to a fits format code
    :param format_code: dtype parameter from numpy array
    :return: string: Fits type code
    """
    format_code = ''
    if 'bool' in format_code.name:
        format_code = 'L'
    elif np.issubdtype(format_code, np.int16):
        format_code = 'I'
    elif np.issubdtype(format_code, np.int32):
        format_code = 'J'
    elif np.issubdtype(format_code, np.int64):
        format_code = 'K'
    elif np.issubdtype(format_code, np.float32):
        format_code = 'E'
    elif np.issubdtype(format_code, np.float64):
        format_code = 'D'
    elif np.issubdtype(format_code, np.complex32):
        format_code = 'C'
    elif np.issubdtype(format_code, np.complex64):
        format_code = 'M'
    elif np.issubdtype(format_code, np.character):
        format_code = 'A'
    return format_code


def table_to_fits(table):
    """
    Convert an astropy table to a fits binary table HDU
    :param table: astropy table
    :return: fits BinTableHDU
    """
    columns = [fits.Column(name=col.upper(), format=fits_formats(table[col].dtype),
                           array=table[col]) for col in table.colnames]
    return fits.BinTableHDU.from_columns(columns)


def open_image(filename):
    base_filename = os.path.basename(filename)
    if filename[-3:] == '.fz':
        # Strip off the .fz
        output_filename = os.path.join(tempfile.tempdir, base_filename)[:-2]
        os.system('funpack {0} -O {1}'.format(filename, output_filename))
        fits_filename = output_filename
        fpacked = True
    else:
        fits_filename = filename
        fpacked = False
    hdu = fits.open(fits_filename, 'readonly')
    data = hdu[0].data.astype(np.float32)
    header = hdu[0].header
    try:
        bpm = hdu['BPM'].data
    except KeyError:
        bpm = None
    hdu.close()
    if fpacked:
        shutil.remove(fits_filename)
    return data, header, bpm