from setuptools import setup, find_packages

setup(
    name="vqc-monitor",
    version="1.0.0",
    packages=find_packages(),
    include_package_data=True,
    install_requires=open("requirements.txt").read().splitlines(),
    entry_points={
        "console_scripts": [
            "vqc-monitor = vqc-monitor.main:main"
        ]
    },
)
