r"""
Learned Iterative Soft-Thresholding Algorithm (LISTA) for compressed sensing
====================================================================================================

This example shows how to implement LISTA for a compressed sensing problem.

"""
from pathlib import Path

import numpy as np
import torch
from torchvision import datasets
from torchvision import transforms

import deepinv as dinv
from torch.utils.data import DataLoader
from deepinv.datasets import mnist_dataloader
from deepinv.models.denoiser import Denoiser
from deepinv.optim.data_fidelity import L2
from deepinv.unfolded import Unfolded
from deepinv.training_utils import train, test
from deepinv.utils.demo import load_dataset

import matplotlib.pyplot as plt

# %%
# Setup paths for data loading and results.
# ----------------------------------------------------------------------------------------
#

BASE_DIR = Path(".")
ORIGINAL_DATA_DIR = BASE_DIR / "datasets"
DATA_DIR = BASE_DIR / "measurements"
RESULTS_DIR = BASE_DIR / "results"
CKPT_DIR = BASE_DIR / "ckpts"

# Set the global random seed from pytorch to ensure reproducibility of the example.
torch.manual_seed(0)

device = dinv.utils.get_freer_gpu() if torch.cuda.is_available() else "cpu"

# %%
# Load base image datasets and degradation operators.
# ----------------------------------------------------------------------------------------
# In this example, we use the MNIST dataset and we consider a compressed sensing problem.

img_size = 28
n_channels = 1
operation = "compressed-sensing"
train_dataset_name = "MNIST_train"
test_dataset_name = "MNIST_test"

# Generate training and evaluation datasets in HDF5 folders and load them.
train_transform = transforms.Compose([transforms.ToTensor()])
test_transform = transforms.Compose([transforms.ToTensor()])
train_base_dataset = datasets.MNIST(
    root=ORIGINAL_DATA_DIR, train=True, transform=train_transform, download=True
)
test_base_dataset = datasets.MNIST(
    root=ORIGINAL_DATA_DIR, train=False, transform=train_transform, download=True
)


# %%
# Generate a dataset of low resolution images and load it.
# ----------------------------------------------------------------------------------------
# We use the Compressed sensing class from the physics module to generate a dataset of low dimension measurements.


# Use parallel dataloader if using a GPU to fasten training, otherwise, as all computes are on CPU, use synchronous
# dataloading.
num_workers = 4 if torch.cuda.is_available() else 0

# Degradation parameters

# Generate the compressed sensing measurement operator
physics = dinv.physics.CompressedSensing(
    m=78, img_shape=(n_channels, img_size, img_size), device=device
)
my_dataset_name = "demo_LISTA"
n_images_max = (
    1000 if torch.cuda.is_available() else 200
)  # maximal number of images used for training
measurement_dir = DATA_DIR / train_dataset_name / operation
generated_datasets_path = dinv.datasets.generate_dataset(
    train_dataset=train_base_dataset,
    test_dataset=test_base_dataset,
    physics=physics,
    device=device,
    save_dir=measurement_dir,
    train_datapoints=n_images_max,
    test_datapoints=8,
    num_workers=num_workers,
    dataset_filename=str(my_dataset_name),
)

train_dataset = dinv.datasets.HDF5Dataset(path=generated_datasets_path, train=True)
test_dataset = dinv.datasets.HDF5Dataset(path=generated_datasets_path, train=False)

# %%
# Define the unfolded Proximal algorithm.
# ----------------------------------------------------------------------------------------
# We use the Unfolded class to define the unfolded PnP algorithm.
# For both 'stepsize' and 'g_param', if initialized with a table of length max_iter, then a distinct stepsize/g_param
# value is learned for each iteration.

# Select the data fidelity term
data_fidelity = L2()

# Set up the trainable denoising prior; here, the soft-threshold in a wavelet basis.
denoiser_spec = {
    "name": "waveletprior",
    "args": {"wv": "db4", "level": 2, "device": device},
}


# If the prior dict value is initialized with a table of lenght max_iter, then a distinct model is trained for each
# iteration. For fixed trained model prior across iterations, initialize with a single model.
max_iter = 30 if torch.cuda.is_available() else 20  # Number of unrolled iterations
prior = {"prox_g": [Denoiser(denoiser_spec) for i in range(max_iter)]}

# Unrolled optimization algorithm parameters
lamb = [1.0] * max_iter  # initialization of the regularization parameter
stepsize = [1.0] * max_iter  # initialization of the stepsizes.
sigma_denoiser = [0.1] * max_iter  # initialization of the denoiser parameters
params_algo = {  # wrap all the restoration parameters in a 'params_algo' dictionary
    "stepsize": stepsize,
    "g_param": sigma_denoiser,
    "lambda": lamb,
}

trainable_params = [
    "g_param",
    "stepsize",
]  # define which parameters from 'params_algo' are trainable

# Define the unfolded trainable model.
model = Unfolded(
    "PGD",
    params_algo=params_algo,
    trainable_params=trainable_params,
    data_fidelity=data_fidelity,
    max_iter=max_iter,
    prior=prior,
)

# %%
# Define the training parameters.
# -------------------------------
# We use the Adam optimizer.


# training parameters
epochs = 100 if torch.cuda.is_available() else 10
learning_rate = 1e-3
train_batch_size = 32 if torch.cuda.is_available() else 8
test_batch_size = 32 if torch.cuda.is_available() else 8

# choose optimizer and scheduler
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=0.0)

# choose supervised training loss
losses = [dinv.loss.SupLoss(metric=dinv.metric.mse())]

# Logging parameters
verbose = True
wandb_vis = False  # plot curves and images in Weight&Bias

train_dataloader = DataLoader(
    train_dataset, batch_size=train_batch_size, num_workers=num_workers, shuffle=True
)
test_dataloader = DataLoader(
    test_dataset, batch_size=test_batch_size, num_workers=num_workers, shuffle=False
)

# %%
# Train the network
# -----------------
# We train the network using the library's train function.

train(
    model=model,
    train_dataloader=train_dataloader,
    eval_dataloader=test_dataloader,
    epochs=epochs,
    # scheduler=scheduler,
    losses=losses,
    physics=physics,
    optimizer=optimizer,
    device=device,
    save_path=str(CKPT_DIR / operation),
    verbose=verbose,
    wandb_vis=wandb_vis,
)

# %%
# Test the network
# ----------------
#
# We now test the learned unrolled network on the test dataset.
#

plot_images = True
save_images = True
method = "unfolded_pgd"

test(
    model=model,
    test_dataloader=test_dataloader,
    physics=physics,
    device=device,
    plot_images=plot_images,
    save_images=save_images,
    save_folder=RESULTS_DIR / method / operation,
    verbose=verbose,
    wandb_vis=wandb_vis,
)


# %%
# Printing the weights of the network
# -----------------------------------
#
# We now plot the weights of the network that were learned and check that they are different from their initilisation values.
#

list_g_param = [
    name_param[1].item()
    for i, name_param in enumerate(model.named_parameters())
    if name_param[1].requires_grad and "g_param" in name_param[0]
]
list_stepsize = [
    name_param[1].item()
    for i, name_param in enumerate(model.named_parameters())
    if name_param[1].requires_grad and "stepsize" in name_param[0]
]

# Create a figure and axes
fig, ax = plt.subplots()

# Plot the data
ax.plot(np.arange(len(list_g_param)), list_g_param, label="g_param", color="r")
ax.plot(np.arange(len(list_stepsize)), list_stepsize, label="stepsize", color="b")

# Set labels and title
ax.set_xlabel("Layer index")
ax.set_ylabel("Value")

ax.grid(True, linestyle="--", alpha=0.5)
ax.legend()
plt.show()
