# -*- coding: utf-8 -*-  # noqa: D100

from pathlib import Path
import re

from setuptools import find_packages, setup


# get version from __version__ variable in kefiya/__init__.py
def get_version():
    init_py = Path("kefiya/__init__.py")
    if init_py.exists():
        content = init_py.read_text()
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]*)['\"]", content, re.M)
        if match:
            return match.group(1)
    return "0.1.0"


setup(
    name="kefiya",
    version=get_version(),
    description="FinTS Connector for ERPNext (Germany)",
    author="Phamos GmbH",
    author_email="support@phamos.eu",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
)
