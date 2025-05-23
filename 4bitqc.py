import cirq
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import matplotlib.pyplot as plt
from scipy.optimize import minimize
import logging
import gc
import faulthandler
import psutil
import os

# Enable faulthandler for segmentation fault stack trace
faulthandler.enable()

# Setup logging
logging.basicConfig(level=logging.INFO, filename='qgan.log', filemode='w',
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Function to log memory usage
def log_memory_usage():
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    logging.info(f"Memory usage: RSS={mem_info.rss / 1024**2:.2f}MB, VMS={mem_info.vms / 1024**2:.2f}MB")

# 1. Generate real data (Gaussian distribution)
np.random.seed(42)
real_data = np.random.normal(0, 1, 10000)
real_data = torch.tensor(real_data, dtype=torch.float32).reshape(-1, 1)

# 2. Quantum Generator (Cirq)
n_qubits = 4
qubits = [cirq.GridQubit(0, i) for i in range(n_qubits)]
n_params = 4 * n_qubits  # Deeper circuit for expressiveness

def create_generator_circuit(params):
    circuit = cirq.Circuit()
    for i in range(n_qubits):
        circuit.append(cirq.rx(params[4*i]).on(qubits[i]))
        circuit.append(cirq.rz(params[4*i + 1]).on(qubits[i]))
        circuit.append(cirq.ry(params[4*i + 2]).on(qubits[i]))
        circuit.append(cirq.rz(params[4*i + 3]).on(qubits[i]))
        if i < n_qubits - 1:
            circuit.append(cirq.CNOT(qubits[i], qubits[i + 1]))
    circuit.append(cirq.measure(*qubits, key='m'))
    return circuit

def sample_generator(params, n_samples=50):
    try:
        log_memory_usage()
        circuit = create_generator_circuit(params)
        simulator = cirq.Simulator()  # Standard simulator
        # Alternative: Use DensityMatrixSimulator if crashes persist (uncomment below)
        # simulator = cirq.DensityMatrixSimulator()
        resolver = {f'theta_{i}': params[i] for i in range(len(params))}
        results = simulator.run(circuit, param_resolver=resolver, repetitions=n_samples)
        measurements = results.measurements['m']
        values = np.sum(measurements * (2 ** np.arange(n_qubits)[::-1]), axis=1) / (2 ** (n_qubits - 1)) - 1
        samples = torch.tensor(values, dtype=torch.float32).reshape(-1, 1)
        torch.cuda.empty_cache()
        gc.collect()
        log_memory_usage()
        return samples
    except Exception as e:
        logging.error(f"Generator sampling failed: {e}")
        raise

# 3. Classical Discriminator (PyTorch)
class Discriminator(nn.Module):
    def __init__(self):
        super(Discriminator, self).__init__()
        self.model = nn.Sequential(
            nn.Linear(1, 64),
            nn.ReLU(),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid()
        )
    
    def forward(self, x):
        return self.model(x)

discriminator = Discriminator()
d_optimizer = optim.Adam(discriminator.parameters(), lr=0.0005)
criterion = nn.BCELoss()

# 4. QGAN Training
def train_discriminator(real_samples, fake_samples):
    try:
        log_memory_usage()
        d_optimizer.zero_grad()
        real_labels = torch.ones(real_samples.size(0), 1) * 0.9  # Label smoothing
        fake_labels = torch.zeros(fake_samples.size(0), 1)
        
        real_output = discriminator(real_samples)
        d_loss_real = criterion(real_output, real_labels)
        
        fake_output = discriminator(fake_samples.detach())
        d_loss_fake = criterion(fake_output, fake_labels)
        
        d_loss = (d_loss_real + d_loss_fake) / 2
        d_loss.backward()
        torch.nn.utils.clip_grad_norm_(discriminator.parameters(), max_norm=1.0)
        d_optimizer.step()
        log_memory_usage()
        return d_loss.item()
    except Exception as e:
        logging.error(f"Discriminator training failed: {e}")
        raise

def train_generator(params, n_samples=50):
    try:
        fake_samples = sample_generator(params, n_samples)
        fake_output = discriminator(fake_samples)
        g_loss = criterion(fake_output, torch.ones(n_samples, 1))
        return g_loss.item()
    except Exception as e:
        logging.error(f"Generator training failed: {e}")
        raise

def optimize_generator(params, n_samples=50):
    def objective(params):
        return train_generator(params, n_samples)
    try:
        log_memory_usage()
        result = minimize(objective, params, method='COBYLA', options={'maxiter': 1})
        log_memory_usage()
        return result.x, result.fun
    except Exception as e:
        logging.error(f"Generator optimization failed: {e}")
        raise

# Visualization function
def plot_distributions(params, epoch, real_data):
    try:
        fake_samples = sample_generator(params, 1000).numpy()  # Reduced for memory
        plt.figure(figsize=(8, 6))
        plt.hist(real_data.numpy(), bins=50, alpha=0.5, label='Real (Gaussian)', density=True, color='#00CED1')
        plt.hist(fake_samples, bins=50, alpha=0.5, label='Generated', density=True, color='#FF4500')
        plt.title(f'QGAN: Real vs Generated (Epoch {epoch})', fontsize=14, fontfamily='Montserrat')
        plt.xlabel('Value', fontsize=12)
        plt.ylabel('Density', fontsize=12)
        plt.legend()
        plt.savefig(f'qgan_distribution_epoch_{epoch}.png', dpi=300, bbox_inches='tight')
        plt.close()
    except Exception as e:
        logging.error(f"Plotting failed: {e}")
        raise

# Save losses plot
def plot_losses(g_losses, d_losses, epoch):
    try:
        plt.figure(figsize=(8, 6))
        plt.plot(g_losses, label='Generator Loss', color='#FF4500')
        plt.plot(d_losses, label='Discriminator Loss', color='#00CED1')
        plt.title('QGAN Training Losses', fontsize=14, fontfamily='Montserrat')
        plt.xlabel('Epoch', fontsize=12)
        plt.ylabel('Loss', fontsize=12)
        plt.legend()
        plt.savefig(f'qgan_losses_epoch_{epoch}.png', dpi=300, bbox_inches='tight')
        plt.close()
    except Exception as e:
        logging.error(f"Loss plotting failed: {e}")
        raise

# Training Loop with Early Stopping
n_epochs = 100
batch_size = 50
initial_params = np.random.randn(n_params) * 0.1
params = initial_params
g_losses, d_losses = [], []
best_g_loss = float('inf')
patience, max_patience = 0, 20
loss_window = 5  # For smoothing

try:
    for epoch in range(n_epochs):
        idx = np.random.choice(len(real_data), batch_size, replace=False)
        real_samples = real_data[idx]
        fake_samples = sample_generator(params, batch_size)
        
        d_loss = train_discriminator(real_samples, fake_samples)
        params, g_loss = optimize_generator(params, batch_size)
        
        g_losses.append(g_loss)
        d_losses.append(d_loss)
        
        # Smooth losses for early stopping
        if len(g_losses) >= loss_window:
            smoothed_g_loss = np.mean(g_losses[-loss_window:])
        else:
            smoothed_g_loss = g_loss
        
        logging.info(f"Epoch {epoch}, D Loss: {d_loss:.4f}, G Loss: {g_loss:.4f}, Smoothed G Loss: {smoothed_g_loss:.4f}")
        print(f"Epoch {epoch}, D Loss: {d_loss:.4f}, G Loss: {g_loss:.4f}")
        
        # Save plots every epoch
        plot_distributions(params, epoch, real_data)
        plot_losses(g_losses, d_losses, epoch)
        
        # Early stopping
        if smoothed_g_loss < best_g_loss:
            best_g_loss = smoothed_g_loss
            patience = 0
        else:
            patience += 1
        if patience >= max_patience:
            logging.info(f"Early stopping at epoch {epoch}")
            print(f"Early stopping at epoch {epoch}")
            break
        
        torch.cuda.empty_cache()
        gc.collect()
except Exception as e:
    logging.error(f"Training loop failed: {e}")
    print(f"Training stopped: {e}")
    # Save partial results on crash
    try:
        plot_distributions(params, epoch + 1, real_data)
        plot_losses(g_losses, d_losses, epoch + 1)
    except Exception as e:
        logging.error(f"Partial plotting failed: {e}")

# Final Plots
try:
    plot_distributions(params, n_epochs, real_data)
    plot_losses(g_losses, d_losses, n_epochs)
except Exception as e:
    logging.error(f"Final plotting failed: {e}")
    print(f"Final plotting failed: {e}")