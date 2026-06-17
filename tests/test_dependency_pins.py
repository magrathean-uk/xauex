from pathlib import Path


def test_requirements_pin_numpy_to_pre_x86_v2_wheels():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()

    numpy_lines = [
        line.strip()
        for line in requirements
        if line.strip().startswith("numpy")
    ]

    assert numpy_lines == ["numpy>=2.2.6,<2.3"]
