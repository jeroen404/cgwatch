from setuptools import setup, find_packages

setup(
    name="cgwatch",
    version="0.2.0",
    description="CGroup Memory Watcher and TUI for the Linux desktop",
    author="Jeroen",
    url="https://github.com/jeroen404/cgwatch",
    packages=["cgwatch"],
    include_package_data=True,
    python_requires=">=3.10",
    install_requires=[
        "textual",
        "humanize",
    ],
    entry_points={
        "console_scripts": [
            "cgwatcher=cgwatcher:main",
        ],
    },
)