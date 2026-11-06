# training

Google Colab notebooks that train the YOLO detector. Select a GPU runtime and run the cells in order, once each. Datasets are uploaded to Colab and are not in this repo.

| Notebook | Classes | Output |
|---|---|---|
| `train_oggetti_colab.ipynb` | 4: `tappo_bianco`, `anello_verde`, `vite_arancione`, `secchio_blu` | Weights for the demo objects only |
| `train_oggetti_mani_colab.ipynb` | 7: the four objects plus arm, hand and wrist | `oggetti_mani.pt`, `oggetti_mani.onnx` |

The 7-class notebook takes `dataset_motiq_1000.zip`. Its validation set is 44 unmodified real photos, which decide early stopping and `best.pt`. Copy the resulting `oggetti_mani.onnx` to [`data/models/`](../data/models).
