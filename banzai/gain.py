import logging

from banzai.stages import Stage

logger = logging.getLogger('banzai')


class GainNormalizer(Stage):
    def __init__(self, runtime_context):
        super(GainNormalizer, self).__init__(runtime_context)

    def do_stage(self, image):
        for i, data in enumerate(image.ccd_hdus):
            data *= data.gain
            logger.info(f'Multiplying by gain of {data.gain:.1f} from extension {i+1:d}', image=image)
        return image
