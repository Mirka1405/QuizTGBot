import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO

def generate_spidergram(categories, values, title):
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
    ax.plot(angles, values, color='blue', linewidth=2, linestyle='solid')
    ax.fill(angles, values, color='blue', alpha=0.25)
    
    # Customize axes
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_thetagrids(np.degrees(angles[:-1]), labels=categories)
    
    # Set y-axis
    max_val = max(values)
    ax.set_ylim(0, max_val * 1.1)
    ax.set_yticks(np.linspace(0, max_val, 5))
    ax.grid(True)
    
    # Add title
    ax.set_title(title, size=16, pad=20)
    
    # Save to buffer
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='png', dpi=96, bbox_inches='tight')
    img_buffer.seek(0)
    plt.close()
    
    return img_buffer