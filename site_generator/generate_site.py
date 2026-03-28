"""
Static site generator for normalized business data.
Converts JSON output from the scraper into HTML landing pages.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from jinja2 import Environment, FileSystemLoader, Template


def load_normalized_json(json_path: str) -> Dict[str, Any]:
    """
    Load normalized JSON data from file.
    
    Args:
        json_path: Path to the normalized JSON file
        
    Returns:
        Dictionary containing normalized business data
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # The normalized data is under the 'normalized' key
    if 'normalized' in data:
        return data['normalized']
    return data


def render_template(template_dir: str, template_name: str, data: Dict[str, Any]) -> str:
    """
    Render Jinja2 template with provided data.
    
    Args:
        template_dir: Directory containing templates
        template_name: Name of template file (e.g., 'index.html')
        data: Data to inject into template
        
    Returns:
        Rendered HTML string
    """
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template(template_name)
    return template.render(**data)


def build_site(json_path: str, template_name: str = 'modern_landing', output_base_dir: str = 'site_generator/output') -> str:
    """
    Build static HTML site from normalized JSON data.
    
    Args:
        json_path: Path to the normalized JSON file
        template_name: Name of template subdirectory under templates/
        output_base_dir: Base output directory (default: 'site_generator/output')
        
    Returns:
        Path to the generated HTML file
    """
    # Load normalized data
    data = load_normalized_json(json_path)
    
    # Determine output directory based on domain
    domain = data.get('domain', 'site')
    output_dir = Path(output_base_dir) / domain
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Template paths
    template_dir = Path('site_generator/templates') / template_name
    
    # Render HTML
    html = render_template(str(template_dir), 'index.html', data)
    
    # Save HTML
    output_path = output_dir / 'index.html'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    # Copy CSS
    css_source = template_dir / 'style.css'
    css_dest = output_dir / 'style.css'
    if css_source.exists():
        with open(css_source, 'r', encoding='utf-8') as src:
            with open(css_dest, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
    
    return str(output_path)


if __name__ == '__main__':
    # Example usage
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python generate_site.py <json_path> [template_name] [output_dir]")
        sys.exit(1)
    
    json_path = sys.argv[1]
    template_name = sys.argv[2] if len(sys.argv) > 2 else 'modern_landing'
    output_dir = sys.argv[3] if len(sys.argv) > 3 else 'site_generator/output'
    
    try:
        result = build_site(json_path, template_name, output_dir)
        print(f"Site generated: {result}")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)