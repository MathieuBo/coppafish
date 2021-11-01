import numpy as np
import utils.errors
import nd2

# bioformats ssl certificate error solution:
# https://stackoverflow.com/questions/35569042/ssl-certificate-verify-failed-with-python3


def load(file_name):
    """
    returns dask array with indices in order fov, channel, y, x, z

    :param file_name: string, path to desired nd2 file
    :return:
    """
    images = nd2.ND2File(file_name)
    images = images.to_dask()
    # images = nd2.imread(file_name, dask=True)  # get python crashing with this in get_image for some reason
    images = np.moveaxis(images, 1, -1)  # put z index to end
    return images


def get_metadata(file_name):
    """
    returns dictionary containing the keys
        xy_pos: xy position of tiles in pixels. ([nTiles x 2] numpy array)
        pixel_microns: xy pixel size in microns (float)
        pixel_microns_z: z pixel size in microns (float)
        sizes: dictionary with fov (t), channels (c), y, x, z-planes (z) dimensions

    :param file_name: string, path to desired nd2 file
    """
    images = nd2.ND2File(file_name)
    metadata = {'sizes': {'t': images.sizes['P'], 'c': images.sizes['C'], 'y': images.sizes['Y'],
                          'x': images.sizes['X'], 'z': images.sizes['Z']},
                'pixel_microns': images.metadata.channels[0].volume.axesCalibration[0],
                'pixel_microns_z': images.metadata.channels[0].volume.axesCalibration[2]}
    xy_pos = np.array([images.experiment[0].parameters.points[i].stagePositionUm[:2]
                       for i in range(images.sizes['P'])])
    metadata['xy_pos'] = (xy_pos - np.min(xy_pos, 0)) / metadata['pixel_microns']
    return metadata


def get_image(images, fov, channel, use_z=None):
    """

    :param images: dask array with fov, channel, y, x, z as index order.
    :param fov: fov index of desired image
    :param channel: channel of desired image
    :param use_z: integer list, optional
        which z-planes of image to load
        default: will load all z-planes
    :return: 3D numpy uint16 array
    """
    if use_z is None:
        use_z = np.arange(images.shape[-1])
    return np.asarray(images[fov, channel, :, :, use_z])



'''with nd2reader'''
# from nd2reader import ND2Reader
#
#
# def load(file_name):
#     """
#     :param file_name: path to desired nd2 file
#     :return: ND2Reader object with z index
#              iterating fastest and then channel index
#              and then field of view.
#     """
#     utils.errors.no_file(file_name)
#     images = ND2Reader(file_name)
#     images.iter_axes = 'vcz'
#     return images
#
#
# def get_metadata(file_name):
#     """
#     returns dictionary containing (at the bare minimum) the keys
#         xy_pos: xy position of tiles in pixels. ([nTiles x 2] numpy array)
#         pixel_microns: xy pixel size in microns (float)
#         pixel_microns_z: z pixel size in microns (float)
#         sizes: dictionary with fov (t), channels (c), y, x, z-planes (z) dimensions
#
#     :param file_name: path to desired nds2 file
#     """
#     images = load(file_name)
#     images = update_metadata(images)
#     return images.metadata
#
#
# def get_image(images, fov, channel, use_z=None):
#     """
#     get image as numpy array from nd2 file
#
#     :param images: ND2Reader object with fov, channel, z as index order.
#     :param fov: fov index of desired image
#     :param channel: channel of desired image
#     :param use_z: integer list, optional
#         which z-planes of image to load
#         default: will load all z-planes
#     :return: 3D numpy array
#     """
#     if use_z is None:
#         use_z = np.arange(images.sizes['z'])
#     image = np.zeros((images.sizes['x'], images.sizes['y'], len(use_z)), dtype=np.uint16)
#     start_index = fov * images.sizes['c'] * images.sizes['z'] + channel * images.sizes['z']
#     for i in range(len(use_z)):
#         image[:, :, i] = images[start_index + use_z[i]]
#     return image
#
#
# def update_metadata(images):
#     """
#     Updates metadata dictionary in images to include:
#     pixel_microns_z: z pixel size in microns (float)
#     xy_pos: xy position of tiles in pixels. ([nTiles x 2] numpy array)
#     sizes: dictionary with fov (t), channels (c), y, x, z-planes (z) dimensions
#
#     :param images: ND2Reader object with metadata dictionary
#     """
#     if 'pixel_microns_z' not in images.metadata:
#         # NOT 100% SURE THIS IS THE CORRECT VALUE!!
#         images.metadata['pixel_microns_z'] = \
#             images.parser._raw_metadata.image_calibration[b'SLxCalibration'][b'dAspect']
#     if 'xy_pos' not in images.metadata:
#         images.metadata['xy_pos'] = np.zeros((images.sizes['v'], 2))
#         for i in range(images.sizes['v']):
#             images.metadata['xy_pos'][i, 0] = images.parser._raw_metadata.x_data[i * images.sizes['z']]
#             images.metadata['xy_pos'][i, 1] = images.parser._raw_metadata.y_data[i * images.sizes['z']]
#         images.metadata['xy_pos'] = (images.metadata['xy_pos'] - np.min(images.metadata['xy_pos'], 0)
#                                      ) / images.metadata['pixel_microns']
#     if 'sizes' not in images.metadata:
#         images.metadata['sizes'] = {'t': images.sizes['v'], 'c': images.sizes['c'], 'y': images.sizes['y'],
#                                     'x': images.sizes['x'], 'z': images.sizes['z']}
#     return images