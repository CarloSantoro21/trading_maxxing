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
3. **KV cache en la inferencia autorregresiva** (issue #62). Todos los parches van
   marcados con `# BEGIN trading_maxxing patch (#62) ... # END trading_maxxing patch`:
   - `module.py`: `RotaryPositionalEmbedding.apply_at` rota Q/K con un offset
     de posición; `KVCacheLayer`/`make_kv_cache` guardan K/V por capa;
     `MultiHeadAttentionWithRoPE.forward` y `TransformerBlock.forward` aceptan
     un `cache` opcional (sin cache el camino original queda bit a bit igual).
   - `kronos.py`: `Kronos.decode_s1` acepta `cache=`; `auto_regressive_inference`
     y `KronosPredictor.generate` reciben `use_cache=True` como default, y
     `use_cache=False` conserva el camino previo (lo usa la prueba de equivalencia
     en `tests/test_kronos_kv_cache.py`).

   Si la ventana de contexto llega a rotar (`current_seq_len > max_context`) el
   cache se descarta y se hace un prefill con la ventana actual, porque cada
   posición de RoPE cambia. En producción (400 velas de contexto, `max_context=512`,
   24 velas de predicción) la ventana nunca rota.

Nada más se modificó. El código queda fuera de ruff y de la cobertura (ver `pyproject.toml` raíz);
para actualizarlo, se vuelven a copiar los dos archivos y se reaplican los parches marcados.
