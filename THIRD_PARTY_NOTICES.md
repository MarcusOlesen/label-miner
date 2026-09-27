# Third-party software

Label Miner's own code is MIT-licensed (see [LICENSE](LICENSE)). It depends on the packages below,
which users install themselves with pip. The Windows installer bundles them.

## Python packages

| Package | Licence | Used for |
|---|---|---|
| [Docling](https://github.com/docling-project/docling) (docling, docling-core, docling-ibm-models, docling-parse) | MIT | OCR pipeline and document model |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | Apache-2.0 | text recognition |
| [PyTorch](https://pytorch.org/) / torchvision | BSD-3-Clause | runs the OCR and layout models |
| [Transformers](https://github.com/huggingface/transformers), [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 | model loading |
| [OpenCV](https://opencv.org/) (opencv-python) | Apache-2.0 | image processing inside the OCR engine |
| [NumPy](https://numpy.org/) | BSD-3-Clause | arrays |
| [Pydantic](https://docs.pydantic.dev/) | MIT | Docling's data model |
| [Pillow](https://python-pillow.org/) | MIT-CMU | images |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) | BSD-3-Clause (bundled libraries: see below) | iPhone photos (HEIC) |
| [Requests](https://requests.readthedocs.io/) | Apache-2.0 | webshop lookups |
| [openpyxl](https://openpyxl.readthedocs.io/) | MIT | Excel export |
| [sv-ttk](https://github.com/rdbende/Sun-Valley-ttk-theme) | MIT | dark theme |
| [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) | MIT | drag and drop |
| Python / Tkinter | PSF | runtime and GUI toolkit |

## Models

The installer bundles two model sets, downloaded by `packaging/fetch_models.ps1`. Both are licensed
under Apache-2.0:

- **docling-layout-heron**, from the Docling project.
- **RapidOCR's PP-OCR models**, from PaddleOCR.

## Native libraries in pillow-heif

The pillow-heif wheels for Windows include native libraries with other licences:

- **libheif and libde265 (LGPL-3.0).** These may be redistributed, including commercially. The
  distributor must include their licence texts and allow users to replace the libraries.
- **x265 (GPL-2.0).** This is an HEVC *encoder*. Label Miner only reads (decodes) HEIC photos, but
  the library still ships inside the wheel, and therefore inside the frozen app.

This doesn't affect the source code in this repository, since users install pillow-heif themselves.
A binary that is sold or otherwise distributed commercially should do one of these:

- use a pillow-heif build without x265, or
- convert HEIC photos some other way.

HEVC decoding may also be covered by patents in some countries.

## Build tools

These are not part of the source:

- **PyInstaller.** GPL-2.0 with a bootloader exception that allows distributing frozen apps under
  any licence.
- **Inno Setup.** Its licence allows distributing the installers it creates.

When distributing the built app, include these packages' licence texts. PyInstaller copies most of
them into `dist\Label-Miner\_internal\`.

## The webshop

Label Miner reads the public product data of the shop it was built for (`SHOP_URL` in
`src/label_miner/config.py`). Get the shop's permission before using it for anything else.
