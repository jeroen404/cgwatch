from setuptools import setup, find_packages

setup(
    name="cgwatch",
    version="0.1.0",
    packages=find_packages(),
    # Scripts are handled manually in debian/rules to rename them
)