from .. import utils
from .. import find_spots as fs
from tqdm import tqdm
import numpy as np
from ..setup.notebook import NotebookPage
import os
import warnings


def find_spots(config: dict, nbp_file: NotebookPage, nbp_basic: NotebookPage, auto_thresh: np.ndarray) -> NotebookPage:
    """
    This function turns each tiff file in the tile directory into a point cloud, saving the results
    as `spot_details` in the `find_spots` notebook page.

    See `'find_spots'` section of `notebook_comments.json` file
    for description of the variables in the page.

    Args:
        config: Dictionary obtained from `'find_spots'` section of config file.
        nbp_file: `file_names` notebook page
        nbp_basic: `basic_info` notebook page
        auto_thresh: `float [n_tiles x n_rounds x n_channels]`.
            `auto_thresh[t, r, c]` is the threshold for the tiff file corresponding to tile `t`, round `r`, channel `c`
            such that all local maxima with pixel values greater than this are considered spots.

    Returns:
        `NotebookPage[find_spots]` - Page containing point cloud of all tiles, rounds and channels.
    """
    nbp = NotebookPage("find_spots")
    if nbp_basic.is_3d is False:
        # set z details to None if using 2d pipeline
        config['radius_z'] = None
        config['isolation_radius_z'] = None
        max_spots = config['max_spots_2d']
    else:
        max_spots = config['max_spots_3d']

    # record threshold for isolated spots in each tile of reference round/channel
    if config['isolation_thresh'] is None:
        nbp.isolation_thresh = auto_thresh[:, nbp_basic.ref_round, nbp_basic.anchor_channel] * \
                                  config['auto_isolation_thresh_multiplier']
    else:
        nbp.isolation_thresh = np.ones_like(auto_thresh[:, nbp_basic.ref_round, nbp_basic.anchor_channel]) * \
                                  config['isolation_thresh']

    # Next we load in tiles and rounds as we may want to change these variables
    use_tiles = nbp_basic.use_tiles
    use_rounds = nbp_basic.use_rounds

    # Deal with case where algorithm has been run for some tiles and data saved
    if os.path.isfile(nbp_file.spot_details_info):
        # Load in the saved data
        info = np.load(nbp_file.spot_details_info)
        # Extract the spot_no array
        spot_no = info.f.arr_1
        # The number of tiles is given by the length of dimension 0 of this matrix
        found_tiles_no = spot_no.shape[0]
        # Now save the previously found tiles
        prev_found_tiles = use_tiles[0:found_tiles_no]
        # New use_tiles os the set diff of use_tiles and prev_found_tiles
        use_tiles = np.setdiff1d(use_tiles, prev_found_tiles)
        # Give user a warning
        warnings.warn(f'Already have find_spots results for tiles {prev_found_tiles} so now just running on tiles 'f'{use_tiles}.')
        # Delete this info as it's large and we want to save memory
        del info, spot_no, prev_found_tiles

    # This is the number of images we're going to detect spots on in total
    n_images = len(use_rounds) * len(use_tiles) * len(nbp_basic.use_channels)

    # The use_rounds doesn't include the anchor round, so if we are going to use the anchor round, we must tell the
    # program to include this in the rounds, and do some different analysis on this
    if nbp_basic.use_anchor:
        use_rounds = use_rounds + [nbp_basic.anchor_round]
        # Since there's only one channel for this round, we get len(use_tiles) extra images
        n_images = n_images + len(use_tiles)

    # This is the number of z-planes. To make the code compatible with 2D imaging, we just say that the 2D case is
    # the same as the 3D case but with 1 z-plane
    n_z = np.max([1, nbp_basic.is_3d * nbp_basic.nz])

    with tqdm(total=n_images) as pbar:
        pbar.set_description(f"Detecting spots on filtered images saved as npy")
        # Loop over tiles
        for t in use_tiles:
            # columns of spot_details are: y, x, z
            # max value is y or x coordinate of around 2048 hence can use int16.
            spot_details = np.empty((0, 3), dtype=np.int16)
            # spot_no will be an n_tiles by n_rounds by n_channels matrix, but we add layers throughout the tile for
            # loop so just set first dim = 1, anchor_spots per round are approx 30,000 so use int32
            spot_no = np.zeros((1, nbp_basic.n_rounds + nbp_basic.n_extra_rounds,
                                nbp_basic.n_channels), dtype=np.int32)
            # isolated_spots will be appended to but need to initialise

            for r in use_rounds:

                if r == nbp_basic.anchor_round:
                    use_channels = [nbp_basic.anchor_channel]
                else:
                    use_channels = nbp_basic.use_channels

                for c in use_channels:
                    pbar.set_postfix({'tile': t, 'round': r, 'channel': c})
                    # Find local maxima on shifted uint16 images to save time avoiding conversion to int32.
                    # Then need to shift the detect_spots and check_neighb_intensity thresh correspondingly.
                    image = utils.npy.load_tile(nbp_file, nbp_basic, t, r, c, apply_shift=False)
                    spot_yxz, spot_intensity = fs.detect_spots(image,
                                                               auto_thresh[t, r, c] + nbp_basic.tile_pixel_value_shift,
                                                               config['radius_xy'], config['radius_z'], True)
                    no_negative_neighbour = fs.check_neighbour_intensity(image, spot_yxz,
                                                                         thresh=nbp_basic.tile_pixel_value_shift)
                    spot_yxz = spot_yxz[no_negative_neighbour]
                    spot_intensity = spot_intensity[no_negative_neighbour]
                    # If r is a reference round, we also get info about whether the spots are isolated
                    if r == nbp_basic.ref_round:
                        isolated_spots = bool(fs.get_isolated(image.astype(np.int32) - nbp_basic.tile_pixel_value_shift,
                                                        spot_yxz, nbp.isolation_thresh[t],
                                                        config['isolation_radius_inner'],
                                                        config['isolation_radius_xy'],
                                                        config['isolation_radius_z']))

                    else:
                        # if imaging round, only keep the highest intensity spots on each z plane
                        # as only used for registration
                        keep = np.ones(spot_yxz.shape[0], dtype=bool)
                        # Loop over each z plane
                        for z in range(n_z):
                            if nbp_basic.is_3d:
                                # in_z is a boolean array of length len(spot_yxz) which is only true if the
                                # corresponding spot has z-coord z
                                in_z = spot_yxz[:, 2] == z
                            else:
                                # in 2D this is just all spots lol
                                in_z = np.ones(spot_yxz.shape[0], dtype=bool)

                            # If the number of spots on this z-plane is > max_spots (500 by default for 3D) then we
                            # set the intensity threshold to the 500th most intense spot and take the top 500 values
                            if np.sum(in_z) > max_spots:
                                intensity_thresh = np.sort(spot_intensity[in_z])[-max_spots]
                                keep[np.logical_and(in_z, spot_intensity < intensity_thresh)] = False
                        spot_yxz = spot_yxz[keep]

                    # Now save the spot_details and spot_no and update the progress bar
                    spot_details = spot_yxz
                    spot_no[0, r, c] = spot_yxz.shape[0]
                    pbar.update(1)

            # Now that we've gotten through a complete tile we will
            # append this tile's spot_details info to the spot_detail_info.npz we have saved in the output directory
            if os.path.isfile(nbp_file.spot_details_info):
                # After ran on one tile, need to load in spot_details_info, append and then save again.
                info = np.load(nbp_file.spot_details_info)

                spot_details_read = info.f.arr_0
                spot_no_read = info.f.arr_1
                isolated_spots_read = info.f.arr_2

                spot_details = np.concatenate((spot_details_read, spot_details))
                spot_no = np.concatenate((spot_no_read, spot_no))
                isolated_spots = np.concatenate((isolated_spots_read, isolated_spots))

                np.savez(nbp_file.spot_details_info, spot_details, spot_no, isolated_spots)

                del info, spot_details_read, spot_no_read, isolated_spots_read, spot_details, spot_no, isolated_spots
            # If none then need to create a file. Do that now!
            else:
                # 1st tile, need to create files to save to
                np.savez(nbp_file.spot_details_info, spot_details, spot_no, isolated_spots)
                del spot_details, spot_no, isolated_spots

    # Once all tiles have been run, we load the complete spot_details_info file and save it to the notebook
    info = np.load(nbp_file.spot_details_info)

    spot_details = info.f.arr_0
    spot_no = info.f.arr_1
    isolated_spots = info.f.arr_2

    nbp.spot_details = spot_details
    nbp.spot_no = spot_no
    nbp.isolated_spots = isolated_spots
    return nbp
