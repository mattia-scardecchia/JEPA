import torch
from torch.nn import functional as F
from torch.utils.data import Dataset
from torchvision import datasets
from torchvision import transforms
from typing import Optional
from copy import deepcopy
import os
import json
from .logger import WandbLogger
from .constants import PROJECT, ENTITY


class JepaDataset(Dataset):
    """
    Simple dataset class for JEPA.
    """
    def __init__(self, data: torch.Tensor):
        """
        :param data: tensor of data points. Shape [P, ...]
        """
        self.data = data

    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        x = self.data[idx]
        x_hat = deepcopy(x)  # x_hat should be a corrupted version of x
        return {"x": x, "x_hat": x_hat}


class AutoencoderDataset(Dataset):
    """
    Simple dataset class for autoencoders.
    """
    def __init__(self, data: torch.Tensor):
        """
        :param data: tensor of data points. Shape [P, ...]
        """
        self.data = data

    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, idx):
        x = self.data[idx]
        return {"x": x}


class HiddenManifold:
    """
    This class is a collection of useful methods to generate, save and load
    datasets generated from a hidden manifold model.
    The hidden manifold model is a mathematically tractable model of
    data lying on a low-dimensional manifold embedded in a high-dimensional
    space.
    Data points are generated by sampling random patterns in a latent space
    and projecting them into a larger space through a nonlinear function
    (projection matrix + componentwise nonlinearity).
    """
    def __init__(self, save_dir: str = ""):
        self.save_dir = save_dir
        self.config = None

    @staticmethod
    def get_default_config() -> dict:
        """
        Return the default parameters to use for data generation.
        Here is a list of the parameters:
        - feature_distribution: distribution of the features
        - nonlinearity: nonlinearity applied after projection matrix
        - D: dimension of the latent space
        - N: dimension of the ambient space
        - P: number of data points to generate
        - p: probability of a pattern being active
        - noise: standard deviation of gaussian noise to add to data
        - device: device to use for data generation (cpu or cuda)
        """
        config = {
            "feature_distribution": "gaussian",
            "nonlinearity": "tanh",
            "D": 64,
            "N": 1024,
            "P": 8192,
            "p": 0.5,
            "noise": 0.0,
            "device": "cuda" if torch.cuda.is_available() else "cpu",
        }
        return config
    
    @staticmethod
    def build_config(**kwargs) -> dict:
        """
        Build a configuration dictionary from default parameters.
        Override default parameters with kwargs.
        """
        config = HiddenManifold.get_default_config()
        for key, value in kwargs.items():
            if key not in config:
                raise ValueError(f"Unknown parameter {key}")
            config[key] = value
        return config

    def get_config(self) -> dict:
        """
        Return the config used for the last data generation.
        """
        return self.config
    
    def generate_dataset(self, config: Optional[dict] = None) -> torch.Tensor:
        """
        Generate a random feature dataset using parameters
        specified in config. Expected config parameters are listed
        in get_default_config().
        In a random feature model, data points are the sum of a 
        random subset of a given set of features.
        """
        if config is None:
            config = HiddenManifold.get_default_config()
        self.config = config
        match config["feature_distribution"]:
            case "gaussian":
                feature_generator = torch.randn
            case _:
                raise NotImplementedError
        match config["nonlinearity"]:
            case "relu":
                sigma = F.relu
            case "tanh":
                sigma = F.tanh
            case _:
                raise NotImplementedError
        D, N, P = config["D"], config["N"], config["P"]
        p, noise, device = config["p"], config["noise"], config["device"]

        feature_matrix = feature_generator((D, N))
        latent_patterns = torch.bernoulli(p * torch.ones((P, D)))
        data = sigma(torch.matmul(latent_patterns, feature_matrix) / torch.sqrt(torch.tensor(D)))
        data += noise * torch.randn((P, N))
        return data.to(device)
    
    @staticmethod
    def get_dirname(id: str) -> str:
        """
        Return the name of the directory where to save a dataset with a given id.
        """
        return f"rf_{id}"
    
    def get_filepath(self, id: str) -> str:
        """
        Return the full path to a dataset with a given id.
        """
        filename = self.get_filename(id)
        save_dir = self.save_dir
        return f"{save_dir}/{filename}" if save_dir else filename

    def save_dataset(
            self,
            dataset: torch.Tensor,
            id: str,
            exist_ok: bool = False,
            log_to_wandb: bool = True
        ) -> None:
        """
        Save generated dataset to a file. Important for
        reproducibility of results.
        Place the file in a directory, together with a json file
        with the parameters used for data generation.
        :param dataset: dataset to save
        :param id: unique identifier for the dataset
        :param exist_ok: if False, raise an error if the directory already exists
        :param log_to_wandb: if True, log the dataset to Weights and Biases.
        """
        dataset_dir = os.path.join(self.save_dir, self.get_dirname(id))
        os.makedirs(dataset_dir, exist_ok=exist_ok)
        print(f"Saving dataset in directory {dataset_dir}")
        filepath = os.path.join(dataset_dir, f"dataset.pt")
        torch.save(dataset, filepath)
        metadata = self.get_config()
        metadata["id"] = id
        metadata["dataset_path"] = filepath
        metadata["dataset_dir"] = dataset_dir
        del metadata["device"]
        with open(os.path.join(dataset_dir, "metadata.json"), "w") as f:
            json.dump(metadata, f)
        if log_to_wandb:
            WandbLogger.log_dataset(dataset, metadata, project=PROJECT, entity=ENTITY)

    def load_dataset(self, id: str) -> tuple[torch.Tensor, dict]:
        """
        Load a dataset saved with saved_dataset() in memory.
        """
        dataset_dir = os.path.join(self.save_dir, self.get_dirname(id))
        metadata_path = os.path.join(dataset_dir, "metadata.json")
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        dataset_path = metadata["dataset_path"]
        dataset = torch.load(dataset_path)
        return dataset, metadata
    

def load_mnist_as_dataset(train: bool = True) -> datasets.MNIST:
    """
    Load MNIST as a torch Dataset, with transforms that flatten
    digits and scale pixel intensities to [-1, 1].
    Useful for data exploration and visualization.
    :param train: if True, load the training set, otherwise load the test set
    """
    transform_pipeline = transforms.Compose([
        transforms.ToTensor(),
        transforms.Lambda(lambda x: x.flatten(start_dim=1)),
        transforms.Lambda(lambda x: 2 * (x / 255) - 1),
    ])
    dataset = datasets.MNIST(
        root="data",
        train=train,
        download=True,
        transform=transform_pipeline,
    )
    return dataset


def load_mnist(
    train: bool = True,
    log_to_wandb: bool = False,
    root: str = "data",
    project: str = PROJECT,
) -> tuple[torch.Tensor, dict]:
    """
    Load MNIST dataset, flatten digits and scale pixel intensities to [-1, 1].
    Return it as a tensor, together with metadata.
    Use this to train models.
    :param train: if True, load the training set, otherwise load the test set
    :param log_to_wandb: if True, save the dataset locally and then log it to wandb.
    :param root: root directory where to seek/save the dataset
    :param project: wandb project where to log the dataset
    """
    dataset = datasets.MNIST(
        root=root,
        train=train,
        download=True,
    )
    data = dataset.data
    data = 2 * (data / 255) - 1
    data = data.flatten(start_dim=1)
    split = "train" if train else "test"
    metadata = {"id": f"mnist-{split}", "dataset_dir": "data/MNIST"}
    if log_to_wandb:
        filepath = os.path.join(metadata["dataset_dir"], metadata["id"])
        torch.save(data, filepath)
        WandbLogger.log_dataset(data, metadata, project=project, entity=ENTITY)
    return data, metadata


def load_cifar(train: bool = True, log_to_wandb: bool = False, root: str = "data", project: str = PROJECT, num_classes: int = 10):
    cifar = datasets.CIFAR10 if num_classes == 10 else datasets.CIFAR100
    dataset = cifar(
        root=root,
        train=train,
        download=True,
    )
    data = torch.tensor(dataset.data)
    data = 2 * (data / 255) - 1
    data = data.flatten(start_dim=1)
    split = "train" if train else "test"
    metadata = {"id": f"cifar{num_classes}-{split}", "dataset_dir": f"data/cifar-{num_classes}-batches-py"}
    if log_to_wandb:
        print(f"Logging dataset {metadata['id']} to wandb project {project}.")
        filepath = os.path.join(metadata["dataset_dir"], metadata["id"])
        torch.save(data, filepath)
        WandbLogger.log_dataset(data, metadata, project=project, entity=ENTITY)
    return data, metadata


# # Example usage in main.py

# # generate and save a hidden manifold dataset
# hm = HiddenManifold(save_dir="data")
# config = hm.build_config(noise=0.1)
# dataset = hm.generate_dataset(config)
# id = "test"
# hm.save_dataset(dataset, id, exist_ok=True, log_to_wandb=False)

# # load hidden manifold dataset
# hm = HiddenManifold(save_dir="data")
# id = "test"
# dataset, dataset_metadata = hm.load_dataset(id)
# N = dataset[0].shape[-1]
# log_images = False
# B = 64
