import logging

import numpy as np
import matplotlib.pyplot as plt

from banzai.stages import Stage
from banzai.utils import qc
from banzai.qc.focus import compute_source_gradient, plot_value_vs_position, scatter_sources_vs_position

logger = logging.getLogger('banzai')


class DepthTest(Stage):
    """
    Calculate the zero point as a function of position on the CCD, make a diagnostic plot, and log some statistics
    """
    def __init__(self, runtime_context):
        super(DepthTest, self).__init__(runtime_context)

    def do_stage(self, image):
        sources = image['CAT'].data
        sources['zeropoint'] = sources['catmag'] + 2.5 * np.log10(sources['flux'])
        zp_grad, zp_grad_angle, xctrs, yctrs, Z = compute_source_gradient(sources, 'zeropoint', image.shape, 4)
        logger.info(f'ZP gradient: {zp_grad:.6f} mag/pix at {zp_grad_angle:.0f}°', image=image)

        # make diagnostic plot for observers
        fig = plt.figure(figsize=(8., 4.))
        ax1 = fig.add_subplot(121, projection='3d')
        ax2 = fig.add_subplot(122, projection='3d')
        plot_value_vs_position(xctrs, yctrs, Z, ax=ax1)
        scatter_sources_vs_position(sources, 'zeropoint', image.shape, ax2)
        ax1.set_title('Zero Point')
        fig.tight_layout(pad=2.)
        fig.savefig('image_depth.pdf')
        plt.close(fig)

        qc_results = {'depth_test.background_mean': image.meta['L1MEAN'],
                      'depth_test.background_median': image.meta['L1MEDIAN'],
                      'depth_test.background_stddev': image.meta['L1SIGMA'],
                      'depth_test.filter': image.filter,
                      'depth_test.zeropoint': image.meta['L1ZP'],
                      'depth_test.zeropoint_error': image.meta['L1ZPERR'],
                      'depth_test.color_used': image.meta['L1COLORU'],
                      'depth_test.color_term': image.meta['L1COLOR'],
                      'depth_test.color_term_error': image.meta['L1COLERR'],
                      'depth_test.zeropoint_gradient': zp_grad,
                      'depth_test.zeropoint_gradient_angle': zp_grad_angle}
        qc.save_qc_results(self.runtime_context, qc_results, image)

        return image
