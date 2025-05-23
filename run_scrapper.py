from core import run_scrape

if __name__ == '__main__':
    # Example: run 10 scrapes
    for i in range(10):
        run_scrape(idx=i, output_dir='results')