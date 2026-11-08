# Kronos (vendor)

Copia del código del modelo de [Kronos](https://github.com/shiyu-coder/Kronos)
(Shi et al., *Kronos: A Foundation Model for the Language of Financial Markets*, arXiv 2508.02739),
bajo licencia MIT (ver [LICENSE](LICENSE)).

Kronos no se publica en PyPI (el nombre `kronos` en PyPI es otro paquete, sin relación), así que
incorporamos solo lo necesario para `KronosTokenizer`, `Kronos` y `KronosPredictor`.

- Origen: `model/kronos.py` y `model/module.py`
- Commit: [`67b630e67f6a18c9e9be918d9b4337c960db1e9a`](https://github.com/shiyu-coder/Kronos/tree/67b630e67f6a18c9e9be918d9b4337c960db1e9a/model) (13 de abril de 2026)

## Cambios locales

1. `kronos.py`: `from .module import *` en lugar de `sys.path.append("../")` + `from model.module import *`,
   para poder importarlo como subpaquete.
2. `__init__.py` propio: solo exporta las tres clases (sin `model_dict` ni `get_model_class`).

Nada más se modificó. El código queda fuera de ruff y de la cobertura (ver `pyproject.toml` raíz);
para actualizarlo, se vuelven a copiar los dos archivos y se reaplica el cambio 1.
