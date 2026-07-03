# Orac Voice

Dictado por voz **100% local y privado** para macOS y Windows. Mantienes una tecla, hablas, sueltas: el texto limpio se pega solo donde esté tu cursor. Sin nube, sin suscripción, sin que tu voz salga del computador.

> **English TL;DR:** Local, private push-to-talk dictation for macOS and Windows. Hold a key, speak, release: clean text is pasted at your cursor. Whisper.cpp + Ollama, nothing leaves your machine. Install guides: [macOS](INSTALL-MAC.md) · [Windows](windows/INSTALL.md) · [AI agents](AGENTS.md).

**Pipeline:** mic → whisper.cpp (transcripción local) → Ollama llama3.2:3b (solo quita muletillas) → clipboard + pegado automático.

## Features

- **Push-to-talk**: mantener la tecla (Fn en Mac, Right Ctrl en Windows, rebindeable) y hablar. Doble-tap = manos libres.
- **Bilingüe de verdad**: dicta en español, inglés o mezclado en la misma frase. Un guard determinista garantiza que el limpiador nunca traduzca lo que dijiste.
- **Diccionario propio**: escribe una sigla o marca como debe quedar (ej. `n8n`), graba cómo la pronuncias una vez, y de ahí en adelante se escribe como tú quieres. Se guarda en `dictionary.json`.
- **Limpieza de muletillas**: "um", "eh", "o sea", "ya po" desaparecen; el resto queda EXACTO como lo dijiste (calibrado para español chileno + inglés).
- **Historial local**: página de ajustes con historial, copiar/borrar por dictado. Vive en `history.jsonl`, nunca sale de tu equipo.
- **Nunca pierdes un dictado**: si el limpiador falla o se pasa de listo, se pega el texto crudo de Whisper.

## Instalación

| Plataforma | Guía |
|---|---|
| macOS | [INSTALL-MAC.md](INSTALL-MAC.md) |
| Windows | [windows/INSTALL.md](windows/INSTALL.md) |
| Agente de IA (Claude, etc.) instalándolo por ti | [AGENTS.md](AGENTS.md) |

La carpeta `windows/` es autocontenida: se puede copiar sola a un PC.

## Uso

| Gesto | Qué hace |
|---|---|
| Mantener la tecla + hablar + soltar | Dictado normal |
| Doble-tap | Manos libres (graba sin sostener) |
| Tecla de nuevo (en manos libres) | Detiene y procesa |
| Escape | Cancela el dictado en curso, no pega nada |

**Si no se pegó:** el texto siempre queda en el clipboard → pegar a mano.

## Ajustes (http://127.0.0.1:8091)

En Mac: menú 🎙 → Settings & History. En Windows: doble click al launcher con la app corriendo.

- **Dictation key**: click al botón → aprieta cualquier tecla modificadora → guardada.
- **Microphone**: selector; mics USB que no aceptan 16kHz se remuestrean automático.
- **Language**: Auto / Español / English.
- **Dictionary**: tus palabras propias (siglas, marcas, nombres). Escribir → Record → decirla una vez.
- **History**: colapsable, copiar/borrar por dictado, Clear all con confirmación.

## El log (por dictado)

```
14:32 | rec 3.4s | whisper 812ms | ollama 590ms (ok) | total 1.41s | 142 chars
```

`FALLBACK raw` = se pegó el texto crudo de Whisper (Ollama falló o intentó reescribir). Log en `.tmp/orac.log`.

## Config avanzada (config.json)

`double_tap_ms`, `min_record_s`, `ollama_timeout_s`, `system_prompt` (reglas del limpiador; si comete un error recurrente, agrega el caso textual como ejemplo few-shot).

## Arquitectura

```
flow.py          daemon: hotkey, audio, pipeline, servidor de ajustes (:8091)
pill.py          pastilla flotante (NSPanel en Mac / tkinter en Windows)
settings.html    página de ajustes (servida local, offline)
config.json      configuración
windows/         port completo para Windows, autocontenido
```

Las únicas piezas por plataforma son 4 funciones en `flow.py` (hotkey, clipboard, pegado, sonidos). Todo lo demás es idéntico en Mac y Windows.

## Problemas conocidos

- **Mac, la tecla no hace nada** → System Settings → Privacy & Security → Input Monitoring + Accessibility para tu Terminal (o la app que lo lanza). Cerrar y reabrir después.
- **Whisper alucina en silencio** ("Subtítulos realizados por…"): agregar `-sns --no-speech-thold 0.6` al arranque del server en `ensure_whisper()`.
- **Windows, apps como administrador**: no se puede pegar dentro de ellas (limitación de Windows); el texto queda en el clipboard.

## Licencia

MIT. Las fuentes empaquetadas en `fonts/` (Poppins, Inter, Fraunces) son [SIL Open Font License 1.1](https://openfontlicense.org/).
