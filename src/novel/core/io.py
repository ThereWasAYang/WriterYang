from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel


ModelT = TypeVar("ModelT", bound=BaseModel)


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    return {} if data is None else data


def load_json_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_json(path))


def load_yaml_model(path: Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_yaml(path))

