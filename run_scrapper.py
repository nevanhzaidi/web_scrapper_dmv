# run_scrapper.py

from core import run_scrape
import os
import shutil

def clear_directory(path):
    """
    Remove all files and folders inside `path`, but leave `path` itself intact.
    """
    for entry in os.listdir(path):
        full_path = os.path.join(path, entry)
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)    # recursively delete folder
        else:
            os.remove(full_path) 

if __name__ == '__main__':
    run_dir = 'results'
    clear_directory(run_dir)  # Clear previous results
    for i in range(10):
        run_scrape(idx=i, output_dir=run_dir)
