import numpy as np
from .. import utils
from typing import Tuple, List, Union


def scaled_k_means(x: np.ndarray, initial_cluster_mean: np.ndarray, score_thresh: float = 0, min_cluster_size: int = 10,
                   n_iter: int = 100) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Does a clustering that minimizes the norm of ```x[i] - g[i] * cluster_mean[cluster_ind[i]]```
    for each data point ```i``` in ```x```, where ```g``` is the gain which is not explicitly computed.

    Args:
        x: ```float [n_points x n_dims]```.
            Data set of vectors to build cluster means from.
        initial_cluster_mean: ```float [n_clusters x n_dims]```.
            Starting point of mean cluster vectors.
        score_thresh: Scalar between ```0``` and ```1```.
            Points in ```x``` with dot product to a cluster mean vector greater than this
            contribute to new estimate of mean vector.
        min_cluster_size: If less than this many points assigned to a cluster,
            that cluster mean vector will be set to ```0```.
        n_iter: Maximum number of iterations performed.

    Returns:
        - norm_cluster_mean - ```float [n_clusters x n_dims]```.
            Final normalised mean cluster vectors.
        - cluster_ind - ```int [n_points]```.
            Index of cluster each point was assigned to. ```-1``` means fell below score_thresh and not assigned.
        - cluster_eig_value - ```float [n_clusters]```.
            First eigenvalue of outer product matrix for each cluster.
    """
    # normalise starting points and original data
    norm_cluster_mean = initial_cluster_mean / np.linalg.norm(initial_cluster_mean, axis=1).reshape(-1, 1)
    x_norm = x / np.linalg.norm(x, axis=1).reshape(-1, 1)
    n_clusters = initial_cluster_mean.shape[0]
    n_points, n_dims = x.shape
    cluster_ind = np.ones(x.shape[0], dtype=int) * -2  # set all to -2 so won't end on first iteration
    cluster_eig_val = np.zeros(n_clusters)

    if not utils.errors.check_shape(initial_cluster_mean, [n_clusters, n_dims]):
        raise utils.errors.ShapeError('initial_cluster_mean', initial_cluster_mean.shape, (n_clusters, n_dims))

    for i in range(n_iter):
        cluster_ind_old = cluster_ind.copy()

        # project each point onto each cluster. Use normalized so we can interpret score
        score = np.matmul(x_norm, norm_cluster_mean.transpose())
        cluster_ind = np.argmax(score, axis=1)  # find best cluster for each point
        top_score = score[np.arange(n_points), cluster_ind]
        top_score[np.where(np.isnan(top_score))[0]] = score_thresh-1  # don't include nan values
        cluster_ind[top_score < score_thresh] = -1  # unclusterable points

        if (cluster_ind == cluster_ind_old).all():
            break

        for c in range(n_clusters):
            my_points = x[cluster_ind == c]  # don't use normalized, to avoid overweighting weak points
            n_my_points = my_points.shape[0]
            if n_my_points < min_cluster_size:
                norm_cluster_mean[c] = 0
                continue
            eig_vals, eigs = np.linalg.eig(np.matmul(my_points.transpose(), my_points)/n_my_points)
            best_eig_ind = np.argmax(eig_vals)
            norm_cluster_mean[c] = eigs[:, best_eig_ind] * np.sign(eigs[:, best_eig_ind].mean())  # make them positive
            cluster_eig_val[c] = eig_vals[best_eig_ind]

    return norm_cluster_mean, cluster_ind, cluster_eig_val


def get_bleed_matrix(spot_colors: np.ndarray, initial_bleed_matrix: np.ndarray, method: str, score_thresh: float = 0,
                     min_cluster_size: int = 10, n_iter: int = 100) -> np.ndarray:
    """
    This returns a bleed matrix such that the expected intensity of dye ```d``` in round ```r```
    is a constant multiple of ```bleed_matrix[r, :, d]```.

    Args:
        spot_colors: ```float [n_spots x n_rounds x n_channels]```.
            Intensity found for each spot in each round and channel, normalized in some way to equalize channel
            intensities typically, the normalisation will be such that spot_colors vary between around
            ```-5``` to ```10``` with most near ```0```.
        initial_bleed_matrix: ```float [n_rounds x n_channels x n_dyes]```.
            Initial guess for intensity we expect each dye to produce in each channel and round.
            Should be normalized in same way as spot_colors.
        method: Must be one of the following:

            - ```'single'``` - A single bleed matrix is produced for all rounds.
            - ```'separate'``` - A different bleed matrix is made for each round.
        score_thresh: Scalar between ```0``` and ```1```.
            Threshold used for ```scaled_k_means``` affecting which spots contribute to bleed matrix estimate.
        min_cluster_size: If less than this many points assigned to a dye, that dye mean vector will be set to ```0```.
        n_iter: Maximum number of iterations performed in ```scaled_k_means```.

    Returns:
        ```float [n_rounds x n_channels x n_dyes]```.
            ```bleed_matrix``` such that the expected intensity of dye ```d``` in round ```r```
            is a constant multiple of ```bleed_matrix[r, _, d]```.
    """
    n_rounds, n_channels = spot_colors.shape[1:]
    n_dyes = initial_bleed_matrix.shape[2]
    if not utils.errors.check_shape(initial_bleed_matrix, [n_rounds, n_channels, n_dyes]):
        raise utils.errors.ShapeError('initial_bleed_matrix', initial_bleed_matrix.shape, (n_rounds, n_channels,
                                                                                           n_dyes))

    bleed_matrix = np.zeros((n_rounds, n_channels, n_dyes))  # Round, Measured, Real
    if method.lower() == 'separate':
        for r in range(n_rounds):
            spot_channel_intensity = spot_colors[:, r, :]
            # get rid of any nan codes
            spot_channel_intensity = spot_channel_intensity[~np.isnan(spot_channel_intensity).any(axis=1)]
            dye_codes, _, dye_eig_vals = scaled_k_means(spot_channel_intensity, initial_bleed_matrix[r].transpose(),
                                                        score_thresh, min_cluster_size, n_iter)
            for d in range(n_dyes):
                bleed_matrix[r, :, d] = dye_codes[d] * np.sqrt(dye_eig_vals[d])
    elif method.lower() == 'single':
        initial_bleed_matrix_round_diff = initial_bleed_matrix.max(axis=0) - initial_bleed_matrix.min(axis=0)
        if np.max(np.abs(initial_bleed_matrix_round_diff)) > 1e-10:
            raise ValueError(f"method is {method}, but initial_bleed_matrix is different for different rounds.")

        spot_channel_intensity = spot_colors.reshape(-1, n_channels)
        # get rid of any nan codes
        spot_channel_intensity = spot_channel_intensity[~np.isnan(spot_channel_intensity).any(axis=1)]
        dye_codes, _, dye_eig_vals = scaled_k_means(spot_channel_intensity, initial_bleed_matrix[0].transpose(),
                                                    score_thresh, min_cluster_size, n_iter)
        for r in range(n_rounds):
            for d in range(n_dyes):
                bleed_matrix[r, :, d] = dye_codes[d] * np.sqrt(dye_eig_vals[d])
    else:
        raise ValueError(f"method given was {method} but should be either 'single' or 'separate'")
    return bleed_matrix


def get_dye_channel_intensity_guess(csv_file_name: str, dyes: Union[List[str], np.ndarray],
                                    cameras: Union[List[int], np.ndarray],
                                    lasers: Union[List[int], np.ndarray]) -> np.ndarray:
    """
    This gets an estimate for the intensity of each dye in each channel (before any channel normalisation)
    which is then used as the starting point for the bleed matrix computation.

    Args:
        csv_file_name: Path to csv file which has 4 columns with headers Dye, Camera, Laser, Intensity:

            - Dye is a column of names of different dyes
            - Camera is a column of integers indicating the wavelength in nm of the camera.
            - Laser is a column of integers indicating the wavelength in nm of the laser.
            - Intensity```[i]``` is the approximate intensity of Dye```[i]``` in a channel with Camera```[i]``` and
                Laser```[i]```.
        dyes: ```str [n_dyes]```.
            Names of dyes used in particular experiment.
        cameras: ```int [n_channels]```.
            Wavelength of camera in nm used in each channel.
        lasers: ```int [n_channels]```.
            Wavelength of laser in nm used in each channel.

    Returns:
        ```float [n_dyes x n_channels]```.
            ```[d, c]``` is estimate of intensity of dye ```d``` in channel ```c```.
    """
    n_dyes = len(dyes)
    cameras = np.array(cameras)
    lasers = np.array(lasers)
    n_channels = cameras.shape[0]
    if not utils.errors.check_shape(cameras, lasers.shape):
        raise utils.errors.ShapeError('cameras', cameras.shape, lasers.shape)

    # load in csv info
    csv_dyes = np.genfromtxt(csv_file_name, delimiter=',', usecols=0, dtype=str, skip_header=1)
    csv_cameras = np.genfromtxt(csv_file_name, delimiter=',', usecols=1, dtype=int, skip_header=1)
    csv_lasers = np.genfromtxt(csv_file_name, delimiter=',', usecols=2, dtype=int, skip_header=1)
    csv_intensities = np.genfromtxt(csv_file_name, delimiter=',', usecols=3, dtype=float, skip_header=1)

    # read in intensity from csv info for desired dyes in each channel
    dye_channel_intensity = np.zeros((n_dyes, n_channels))
    for d in range(n_dyes):
        correct_dye = csv_dyes == dyes[d].upper()
        for c in range(n_channels):
            correct_camera = csv_cameras == cameras[c]
            correct_laser = csv_lasers == lasers[c]
            correct_all = np.all((correct_dye, correct_camera, correct_laser), axis=0)
            if sum(correct_all) != 1:
                raise ValueError(f"Expected intensity for dye {dyes[d]}, camera {cameras[c]} and laser {lasers[c]} "
                                 f"to be found once in csv_file. Instead, it was found {sum(correct_all)} times.")
            dye_channel_intensity[d, c] = csv_intensities[np.where(correct_all)[0][0]]

    return dye_channel_intensity