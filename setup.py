from setuptools import find_packages, setup

setup(
    name="waypanel-settings",
    version="1.0",
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'waypanel-settings=waypanel_settings:main'
        ],
    },
    install_requires=[
        "quart>=0.18.0",
        "pywebview>=3.6",
        "tomli>=2.0.0; python_version < '3.11'"
    ],
)
