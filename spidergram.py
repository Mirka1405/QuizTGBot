import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO
from copy import copy

def generate_spidergram(categories, values, title, color='darkblue'):
    """Generate a spidergram image and return it as a BytesIO buffer"""
    num_vars = len(categories)
    
    # Compute angles for each axis
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    
    # Complete the loop
    values += values[:1]
    angles += angles[:1]
    
    # Create figure
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    
    # Plot data
    ax.plot(angles, values, color='blue' if color == "darkblue" else 'red', linewidth=2, linestyle='solid')
    ax.fill(angles, values, color='blue' if color == "darkblue" else 'red', alpha=0.25)
    
    # Customize axes
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_thetagrids(np.degrees(angles[:-1]), labels=categories, fontsize=16)
    
    # Set y-axis
    max_val = max(values)
    ax.set_ylim(0, max_val * 1.1)
    ax.grid(True)
    for angle, value in zip(angles[:-1], values[:-1]):
        ax.annotate(
            f"{value:.1f}",
            xy=(angle, value),
            xytext=(0, -5),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=11,
            fontweight='bold',
            color=color,
            bbox=dict(boxstyle="round,pad=0.2", facecolor="yellow", alpha=0.7, edgecolor="none")
        )
    # Add title
    ax.set_title(title, size=16, pad=20)
    
    # Save to buffer
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='png', dpi=96, bbox_inches='tight')
    img_buffer.seek(0)
    plt.close()
    
    return img_buffer

def generate_double_spidergram(categories,values1,values2,title):
    num_vars = len(categories)
    
    # Compute angles for each axis
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    
    # Complete the loop
    values1 += values1[:1]
    values2 += values2[:1]
    angles += angles[:1]
    
    # Create figure
    fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))
    # Plot data
    ax.plot(angles, values1, color='blue', linewidth=2, linestyle='solid',label='Вся команда')
    ax.fill(angles, values1, color='blue', alpha=0.25)
    ax.plot(angles, values2, color='red', linewidth=2, linestyle='solid',label="Руководитель")
    ax.fill(angles, values2, color='red', alpha=0.25)
    ax.legend()
    
    # Customize axes
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_thetagrids(np.degrees(angles[:-1]), labels=categories, fontsize=16)
    
    # Set y-axis
    max_val = max(values1+values2)
    ax.set_ylim(0, max_val * 1.1)
    for angle, value in zip(angles[:-1], values1[:-1]):
        ax.annotate(
            f"{value:.1f}",
            xy=(angle, value),
            xytext=(0, -5),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=11,
            fontweight='bold',
            color='darkblue',
            bbox=dict(boxstyle="round,pad=0.2", facecolor="yellow", alpha=0.7, edgecolor="none")
        )
    for angle, value in zip(angles[:-1], values2[:-1]):
        ax.annotate(
            f"{value:.1f}",
            xy=(angle, value),
            xytext=(0, -5),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=11,
            fontweight='bold',
            color='darkred',
            bbox=dict(boxstyle="round,pad=0.2", facecolor="yellow", alpha=0.7, edgecolor="none")
        )
    ax.grid(True)
    
    # Add title
    ax.set_title(title, size=16, pad=20)
    
    # Save to buffer
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='png', dpi=96, bbox_inches='tight')
    img_buffer.seek(0)
    plt.close()
    
    return img_buffer