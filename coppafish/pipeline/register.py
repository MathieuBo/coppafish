import os
import pickle
import itertools
import numpy as np
from tqdm import tqdm
from scipy.ndimage import affine_transform
from skimage.registration import phase_cross_correlation
from skimage.filters import gaussian
from .. import utils
from ..setup import NotebookPage
from ..utils.npy import load_tile
from ..find_spots import spot_yxz
from ..register.base import icp, regularise_transforms, round_registration, channel_registration, brightness_scale
from ..register.preprocessing import compose_affine, invert_affine, zyx_to_yxz_affine, yxz_to_zyx_affine, \
    load_reg_data, yxz_to_zyx


def register(nbp_basic: NotebookPage, nbp_file: NotebookPage, nbp_extract: NotebookPage,
             nbp_find_spots: NotebookPage, config: dict, tile_origin: np.ndarray,
             pre_seq_blur_radius: float) -> NotebookPage:
    """
    Registration pipeline. Returns register Notebook Page.
    Finds affine transforms by using linear regression to find the best matrix (in the least squares sense) taking a
    bunch of points in one image to corresponding points in another. These shifts are found with a phase cross
    correlation algorithm.

    To get greater precision with this algorithm, we update these transforms with an iterative closest point algorithm.

    Args:
        nbp_basic: (NotebookPage) Basic Info notebook page
        nbp_file: (NotebookPage) File Names notebook page
        nbp_extract: (NotebookPage) Extract notebook page
        nbp_find_spots: (NotebookPage) Find Spots notebook page
        config: Register part of the config dictionary
        tile_origin: n_tiles x 3 ndarray of tile origins
        pre_seq_blur_radius: Radius of gaussian blur to apply to pre-seq round images
        num_rotations: Number of rotations to apply to each tile

    Returns:
        nbp: (NotebookPage) Register notebook page
        nbp_debug: (NotebookPage) Register_debug notebook page
    """

    # Break algorithm up into initialisation and then 3 parts.

    # Part 0: Initialise variables and load in data from previous runs of the software
    # Part 1: Generate subvolumes, use these in a regression to obtain an initial estimate for affine transform
    # Part 2: Compare the transforms across tiles to remove outlier transforms
    # Part 3: Correct the initial transform guesses with an ICP algorithm

    # Part 0: Initialisation
    # Initialise frequently used variables
    nbp, nbp_debug = NotebookPage("register"), NotebookPage("register_debug")
    use_tiles, use_rounds, use_channels = nbp_basic.use_tiles, nbp_basic.use_rounds, nbp_basic.use_channels
    n_tiles, n_rounds, n_channels = nbp_basic.n_tiles, nbp_basic.n_rounds, nbp_basic.n_channels
    round_registration_channel = config['round_registration_channel']
    if round_registration_channel is None:
        round_registration_channel = nbp_basic.anchor_channel
    # Initialise variables for ICP step
    if nbp_basic.is_3d:
        neighb_dist_thresh = config['neighb_dist_thresh_3d']
    else:
        neighb_dist_thresh = config['neighb_dist_thresh_2d']

    # Load in registration data from previous runs of the software
    registration_data = load_reg_data(nbp_file, nbp_basic, config)
    uncompleted_tiles = np.setdiff1d(use_tiles, registration_data['round_registration']['tiles_completed'])

    # Part 1: Initial affine transform
    # Start with channel registration
    pbar = tqdm(total=len(uncompleted_tiles))
    pbar.set_description(f"Running initial channel registration")
    if registration_data['channel_registration']['transform'].max() == 0:
        if not nbp_basic.channel_camera:
            cameras = [0] * n_channels
        else:
            cameras = list(set(nbp_basic.channel_camera))
        cameras.sort()
        anchor_cam_idx = cameras.index(nbp_basic.channel_camera[nbp_basic.anchor_channel])
        cam_transform = channel_registration(fluorescent_bead_path=nbp_file.fluorescent_bead_path,
                                             anchor_cam_idx=anchor_cam_idx, n_cams=len(cameras),
                                             bead_radii=config['bead_radii'])
        # Now loop through all channels and set the channel transform to its cam transform
        for c in use_channels:
            cam_idx = cameras.index(nbp_basic.channel_camera[c])
            registration_data['channel_registration']['transform'][c] = cam_transform[cam_idx]

    # round registration
    with tqdm(total=len(uncompleted_tiles)) as pbar:
        pbar.set_description(f"Running initial round registration on all tiles")
        for t in uncompleted_tiles:
            # Load in the anchor image and the round images. Note that here anchor means anchor round, not necessarily
            # anchor channel
            anchor_image = yxz_to_zyx(load_tile(nbp_file, nbp_basic, t=t, r=nbp_basic.anchor_round,
                                                c=round_registration_channel))
            use_rounds = nbp_basic.use_rounds + [nbp_basic.pre_seq_round] * nbp_basic.use_preseq
            # split the rounds into two chunks, as we can't fit all of them into memory at once
            round_chunks = [use_rounds[:len(use_rounds) // 2], use_rounds[len(use_rounds) // 2:]]
            for i in range(2):
                round_image = [yxz_to_zyx(load_tile(nbp_file, nbp_basic, t=t, r=r, c=round_registration_channel,
                                                    suffix='_raw' if r == nbp_basic.pre_seq_round else ''))
                               for r in round_chunks[i]]
                round_reg_data = round_registration(anchor_image=anchor_image, round_image=round_image, config=config)
                # Now save the data
                registration_data['round_registration']['transform_raw'][t, round_chunks[i]] = round_reg_data['transform']
                registration_data['round_registration']['shift'][t, round_chunks[i]] = round_reg_data['shift']
                registration_data['round_registration']['shift_corr'][t, round_chunks[i]] = round_reg_data['shift_corr']
                registration_data['round_registration']['position'][t, round_chunks[i]] = round_reg_data['position']
            # Now append anchor info and tile number to the registration data, then save to file
            registration_data['round_registration']['transform'][t, nbp_basic.anchor_round] = np.eye(3, 4)
            registration_data['round_registration']['tiles_completed'].append(t)
            # Save the data to file
            with open(os.path.join(nbp_file.output_dir, 'registration_data.pkl'), 'wb') as f:
                pickle.dump(registration_data, f)
            pbar.update(1)

    # Part 2: Regularisation
    registration_data = regularise_transforms(registration_data=registration_data,
                                              tile_origin=np.roll(tile_origin, 1, axis=1),
                                              residual_threshold=config['residual_thresh'],
                                              use_tiles=nbp_basic.use_tiles,
                                              use_rounds=nbp_basic.use_rounds +
                                                         [nbp_basic.pre_seq_round] * nbp_basic.use_preseq)

    # Now combine all of these into single sub-vol transform array via composition
    for t in use_tiles:
        for r in use_rounds + [nbp_basic.pre_seq_round] * nbp_basic.use_preseq:
            for c in use_channels:
                registration_data['initial_transform'][t, r, c] = \
                    zyx_to_yxz_affine(compose_affine(registration_data['channel_registration']['transform'][c],
                                                     registration_data['round_registration']['transform'][t, r]))
    # Now save registration data externally
    with open(os.path.join(nbp_file.output_dir, 'registration_data.pkl'), 'wb') as f:
        pickle.dump(registration_data, f)

    # Part 3: ICP
    if 'icp' not in registration_data.keys():
        # Initialise variables for ICP step
        icp_transform = np.zeros((n_tiles, n_rounds + nbp_basic.use_anchor + nbp_basic.use_preseq, n_channels, 4, 3))
        n_matches = np.zeros((n_tiles, n_rounds + nbp_basic.use_anchor + nbp_basic.use_preseq, n_channels,
                              config['icp_max_iter']))
        mse = np.zeros((n_tiles, n_rounds + nbp_basic.use_anchor + nbp_basic.use_preseq, n_channels,
                        config['icp_max_iter']))
        converged = np.zeros((n_tiles, n_rounds + nbp_basic.use_anchor + nbp_basic.use_preseq, n_channels), dtype=bool)
        # Create a progress bar for the ICP step
        with tqdm(total=len(use_tiles) * len(use_rounds) * len(use_channels)) as pbar:
            pbar.set_description(f"Running ICP on all tiles")
            for t in use_tiles:
                ref_spots_t = spot_yxz(nbp_find_spots.spot_yxz, t, nbp_basic.anchor_round, nbp_basic.anchor_channel,
                                       nbp_find_spots.spot_no)
                for r, c in itertools.product(use_rounds + [nbp_basic.pre_seq_round] * nbp_basic.use_preseq,
                                              use_channels):
                    pbar.set_postfix({"Tile": t, "Round": r, "Channel": c})
                    # Only do ICP on non-degenerate tiles with more than ~ 100 spots, otherwise just use the
                    # starting transform
                    if nbp_find_spots.spot_no[t, r, c] < config['icp_min_spots'] and r in use_rounds:
                        print(f"Tile {t}, round {r}, channel {c} has too few spots to run ICP. Using initial transform"
                              f" instead.")
                        icp_transform[t, r, c] = registration_data['initial_transform'][t, r, c]
                        continue
                    imaging_spots_trc = spot_yxz(nbp_find_spots.spot_yxz, t, r, c, nbp_find_spots.spot_no)
                    icp_transform[t, r, c], n_matches[t, r, c], mse[t, r, c], converged[t, r, c] = icp(
                        yxz_base=ref_spots_t,
                        yxz_target=imaging_spots_trc,
                        dist_thresh=neighb_dist_thresh,
                        start_transform=registration_data['initial_transform'][t, r, c],
                        n_iters=config['icp_max_iter'],
                        robust=False)
                    pbar.update(1)
        # Save ICP data
        registration_data['icp'] = {'transform': icp_transform, 'n_matches': n_matches, 'mse': mse,
                                    'converged': converged}
        # Save registration data externally
        with open(os.path.join(nbp_file.output_dir, 'registration_data.pkl'), 'wb') as f:
            pickle.dump(registration_data, f)

    # Now blur the pre seq round images
    if registration_data['blur'] is False and nbp_basic.use_preseq:
        for t, c in tqdm(itertools.product(use_tiles, use_channels + [nbp_basic.dapi_channel])):
            print(f" Blurring pre-seq tile {t}, channel {c}")
            # Load in the pre-seq round image, blur it and save it under a different name (dropping the _raw suffix)
            im = load_tile(nbp_file, nbp_basic, t=t, r=nbp_basic.pre_seq_round, c=c, suffix='_raw')
            if pre_seq_blur_radius > 0:
                for z in tqdm(range(len(nbp_basic.use_z))):
                    im[:, :, z] = gaussian(im[:, :, z], pre_seq_blur_radius, truncate=3, preserve_range=True)
            # Save the blurred image (no need to rotate this, as the rotation was done in extract)
            utils.npy.save_tile(nbp_file, nbp_basic, im, t, r, c)
        registration_data['blur'] = True

    # Save registration data externally
    with open(os.path.join(nbp_file.output_dir, 'registration_data.pkl'), 'wb') as f:
        pickle.dump(registration_data, f)
    # Add round statistics to debugging page.
    nbp_debug.position = registration_data['round_registration']['position']
    nbp_debug.round_shift = registration_data['round_registration']['shift']
    nbp_debug.round_shift_corr = registration_data['round_registration']['shift_corr']
    nbp_debug.round_transform_raw = registration_data['round_registration']['transform_raw']

    # Now add the channel registration statistics
    nbp_debug.channel_transform = registration_data['channel_registration']['transform']

    # Now add the ICP statistics
    nbp_debug.mse = registration_data['icp']['mse']
    nbp_debug.n_matches = registration_data['icp']['n_matches']
    nbp_debug.converged = registration_data['icp']['converged']

    # Now add relevant information to the nbp object
    nbp.round_transform = registration_data['round_registration']['transform']
    nbp.channel_transform = registration_data['channel_registration']['transform']
    nbp.initial_transform = registration_data['initial_transform']
    # combine icp transform, channel transform and initial transform to get final transform
    transform = np.zeros((n_tiles, n_rounds + nbp_basic.use_anchor + nbp_basic.use_preseq, n_channels, 4, 3))
    transform[:, use_rounds] = registration_data['icp']['transform'][:, use_rounds]
    if nbp_basic.use_preseq:
        transform[:, nbp_basic.pre_seq_round] = registration_data['icp']['transform'][:, -1]
    nbp.transform = transform

    # Load in the middle z-plane of each tile and compute the scale factors to be used when removing background
    # fluorescence
    if nbp_basic.use_preseq:
        nbp_extract.finalized = False
        del nbp_extract.bg_scale # Delete this so that we can overwrite it
        bg_scale = np.zeros((n_tiles, n_rounds, n_channels))
        mid_z = nbp_basic.tile_centre[2].astype(int)
        z_rad = np.min([len(nbp_basic.use_z) // 2, 5])
        for t, r, c in tqdm(itertools.product(use_tiles, use_rounds, use_channels)):
            print(f"Computing background scale for tile {t}, round {r}, channel {c}")
            transform_pre = yxz_to_zyx_affine(nbp.transform[t, nbp_basic.pre_seq_round, c],
                                              new_origin=np.array([mid_z-z_rad, 0, 0]))
            transform_seq = yxz_to_zyx_affine(nbp.transform[t, r, c], new_origin=np.array([mid_z-z_rad, 0, 0]))
            preseq = yxz_to_zyx(load_tile(nbp_file, nbp_basic, t=t, r=nbp_basic.pre_seq_round, c=c,
                                          yxz=[None, None, np.arange(mid_z-z_rad, mid_z+z_rad)]))
            seq = yxz_to_zyx(load_tile(nbp_file, nbp_basic, t=t, r=r, c=c,
                                       yxz=[None, None, np.arange(mid_z-z_rad, mid_z+z_rad)]))
            preseq = affine_transform(preseq, transform_pre)
            seq = affine_transform(seq, transform_seq)
            bg_scale[t, r, c] = brightness_scale(preseq, seq)[0]
        nbp_extract.bg_scale = bg_scale
        nbp_extract.finalized = True

    return nbp, nbp_debug
