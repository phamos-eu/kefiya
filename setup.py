# -*- coding: utf-8 -*-# noqa: D100

from setuptools import setup, find_packages
import re
import ast

with open('requirements.txt') as f:
    install_requires = f.read().strip().split('\n')

# get version from __version__ variable in kefiya/__init__.py
_version_re = re.compile(r'__version__\s+=\s+(.*)')

with open('kefiya/__init__.py', 'rb') as f:
    version = str(ast.literal_eval(_version_re.search(
        f.read().decode('utf-8')).group(1)))

setup(
    name='kefiya',
    version=version,
    description='FinTS Connector for ERPNext (Germany)',
    author='jHetzer',
    author_email='jHetzer@users.noreply.github.com',
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires
)
