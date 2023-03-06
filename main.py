# Implementation of Consistency Model
# https://arxiv.org/pdf/2303.01469.pdf

import wandb

import argparse
from types import SimpleNamespace
from typing import List
from copy import deepcopy
from tqdm import tqdm
import math

import torch
from torch.optim.lr_scheduler import OneCycleLR
from torchvision.utils import save_image, make_grid

from consistency_models import ConsistencyModel, kerras_boundaries
from consistency_models.utils import get_data

WANDB_PROJECT = "consistency-model"

config = SimpleNamespace(
    img_size = 32,
    batch_size = 64,
    num_workers = 4,
    dataset="mnist",
    lr=1e-3,
    n_epochs=10,
    sample_every_n_epoch=1,
    device="cuda" if torch.cuda.is_available() else "cpu",
    wandb=True,
)

class EMA:
    def __init__(self, model, device=config.device):
        self.model = model
        self.ema_model = deepcopy(model).eval().requires_grad_(False).to(device)

    @torch.inference_mode()
    def update(self, N):
        mu = math.exp(2 * math.log(0.95) / N)
        # update \theta_{-}
        for p, ema_p in zip(self.model.parameters(), self.ema_model.parameters()):
            ema_p.mul_(mu).add_(p, alpha=1 - mu)
    

def train(config):
    dataloader = get_data(config.dataset)
    n_channels = 1 if config.dataset=="mnist" else 3
    model = ConsistencyModel(n_channels, D=256)
    model.to(config.device)
    optim = torch.optim.AdamW(model.parameters())
    scheduler = OneCycleLR(optim, max_lr=config.lr, 
                           steps_per_epoch=len(dataloader), epochs=config.n_epochs)

    ema = EMA(model, device=config.device)

    for epoch in range(1, config.n_epochs):
        N = math.ceil(math.sqrt((epoch * (150**2 - 4) / config.n_epochs) + 4) - 1) + 1
        boundaries = kerras_boundaries(7.0, 0.002, N, 80.0).to(config.device)

        pbar = tqdm(dataloader)
        loss_ema = None
        model.train()
        for x, _ in pbar:
            optim.zero_grad()
            x = x.to(config.device)

            z = torch.randn_like(x)
            t = torch.randint(0, N - 1, (x.shape[0], 1), device=config.device)
            t_0 = boundaries[t]
            t_1 = boundaries[t + 1]

            loss = model.loss(x, z, t_0, t_1, ema_model=ema.ema_model)

            loss.backward()
            if loss_ema is None:
                loss_ema = loss.item()
            else:
                loss_ema = 0.9 * loss_ema + 0.1 * loss.item()

            optim.step()
            scheduler.step()
            ema.update(N)
            if config.wandb:
                wandb.log({"loss": loss.item(),
                           "loss_ema": loss_ema,
                           "lr": scheduler.get_last_lr()[0],
                           "N": N})    
            pbar.set_description(f"loss: {loss_ema:.10f}, N: {N:.10f}")

        model.eval()
        with torch.inference_mode():
            # Sample 5 Steps
            xh = model.sample(
                torch.randn_like(x).to(device=config.device) * 80.0,
                list(reversed([5.0, 10.0, 20.0, 40.0, 80.0])),
            )
            xh = (xh * 0.5 + 0.5).clamp(0, 1)
            grid = make_grid(xh, nrow=4)
            save_image(grid, f"./contents/ct_{config.dataset}_sample_5step_{epoch}.png")
            if config.wandb:
                wandb.log({"sampled_images_5": [wandb.Image(img.permute(1,2,0).squeeze().cpu().numpy()) for img in xh]})
            # Sample 2 Steps
            xh = model.sample(
                torch.randn_like(x).to(device=config.device) * 80.0,
                list(reversed([2.0, 80.0])),
            )
            xh = (xh * 0.5 + 0.5).clamp(0, 1)
            grid = make_grid(xh, nrow=4)
            save_image(grid, f"./contents/ct_{config.dataset}_sample_2step_{epoch}.png")
            if config.wandb:
                wandb.log({"sampled_images_5": [wandb.Image(img.permute(1,2,0).squeeze().cpu().numpy()) for img in xh]})

            # save model
            torch.save(model.state_dict(), f"./ct_{config.dataset}.pth")
            if config.wandb:
                at = wandb.Artifact("model", type="model", description="Model weights for Consistency Model", metadata={"epoch": epoch})
                at.add_file(f"./ct_{config.dataset}.pth")
                wandb.log_artifact(at)


def parse_args(config):
    parser = argparse.ArgumentParser(description='Run training baseline')
    for k,v in config.__dict__.items():
        parser.add_argument('--'+k, type=type(v), default=v)
    args = vars(parser.parse_args())
    
    # update config with parsed args
    for k, v in args.items():
        setattr(config, k, v)

if __name__ == "__main__":
    parse_args(config)
    if config.wandb:
        wandb.init(project=WANDB_PROJECT, config=config)
    train(config)
