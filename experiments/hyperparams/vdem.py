import os
import pandas as pd
import numpy as np
import time
import torch
from torch.utils.data import DataLoader, TensorDataset, random_split
from pytorch_lightning.callbacks import ModelCheckpoint
from pytorch_lightning import Trainer, seed_everything
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import StratifiedKFold, train_test_split
from sklearn.feature_selection import mutual_info_classif, chi2
from sklearn.linear_model import LassoCV
import matplotlib.pyplot as plt
import seaborn as sns

from entropy_lens.models.explainer import Explainer
from entropy_lens.logic.metrics import formula_consistency
from experiments.data.load_datasets import load_vDem


x, c, y, concept_names = load_vDem('../data')

dataset_xc = TensorDataset(x, c)
dataset_cy = TensorDataset(c, y)

train_size = int(len(dataset_cy) * 0.5)
val_size = (len(dataset_cy) - train_size) // 2
test_size = len(dataset_cy) - train_size - val_size
train_data, val_data, test_data = random_split(dataset_cy, [train_size, val_size, test_size])
train_loader = DataLoader(train_data, batch_size=train_size)
val_loader = DataLoader(val_data, batch_size=val_size)
test_loader = DataLoader(test_data, batch_size=test_size)

n_concepts = next(iter(train_loader))[0].shape[1]
n_classes = 2

print(concept_names)
print(n_concepts)
print(n_classes)

# %% md

## 5-fold cross-validation with explainer network

# %%

seed_everything(42)

base_dir = f'./results/vdem/blackbox'
os.makedirs(base_dir, exist_ok=True)

n_splits = 5
results_list = []

for l1 in [1e-3, 1e-4, 1e-5, 1e-6]:
    for tau in [4, 5, 6]:
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        for split, (trainval_index, test_index) in enumerate(skf.split(x.cpu().detach().numpy(),
                                                                       y.argmax(dim=1).cpu().detach().numpy())):
            print(f'Split [{split + 1}/{n_splits}]')
            x_trainval, x_test = torch.FloatTensor(x[trainval_index]), torch.FloatTensor(x[test_index])
            c_trainval, c_test = torch.FloatTensor(c[trainval_index]), torch.FloatTensor(c[test_index])
            y_trainval, y_test = torch.FloatTensor(y[trainval_index]), torch.FloatTensor(y[test_index])
            x_train, x_val, c_train, c_val, y_train, y_val = train_test_split(x_trainval, c_trainval, y_trainval,
                                                                              test_size=0.2, random_state=42)
            print(f'{len(y_train)}/{len(y_val)}/{len(y_test)}')

            # train X->C
            train_data_xc = TensorDataset(x_train, c_train)
            val_data_xc = TensorDataset(x_val, c_val)
            test_data_xc = TensorDataset(x_test, c_test)
            train_loader_xc = DataLoader(train_data_xc, batch_size=train_size)
            val_loader_xc = DataLoader(val_data_xc, batch_size=val_size)
            test_loader_xc = DataLoader(test_data_xc, batch_size=test_size)

            checkpoint_callback_xc = ModelCheckpoint(dirpath=base_dir, monitor='val_loss', save_top_k=1)
            trainer_xc = Trainer(max_epochs=200, gpus=1, auto_lr_find=True, deterministic=True,
                                 check_val_every_n_epoch=1, default_root_dir=base_dir + '_xc',
                                 weights_save_path=base_dir, callbacks=[checkpoint_callback_xc])
            model_xc = Explainer(n_concepts=x.shape[1], n_classes=c.shape[1], l1=0, lr=0.01,
                                 explainer_hidden=[100, 50], temperature=5000, loss=torch.nn.BCEWithLogitsLoss())
            trainer_xc.fit(model_xc, train_loader_xc, val_loader_xc)
            model_xc.freeze()
            c_train_pred = model_xc.model(x_train)
            c_val_pred = model_xc.model(x_val)
            c_test_pred = model_xc.model(x_test)

            # train C->Y
            train_data = TensorDataset(c_train_pred.squeeze(), y_train)
            val_data = TensorDataset(c_val_pred.squeeze(), y_val)
            test_data = TensorDataset(c_test_pred.squeeze(), y_test)
            train_loader = DataLoader(train_data, batch_size=train_size)
            val_loader = DataLoader(val_data, batch_size=val_size)
            test_loader = DataLoader(test_data, batch_size=test_size)

            checkpoint_callback = ModelCheckpoint(dirpath=base_dir, monitor='val_loss', save_top_k=1)
            trainer = Trainer(max_epochs=200, gpus=1, auto_lr_find=True, deterministic=True,
                              check_val_every_n_epoch=1, default_root_dir=base_dir,
                              weights_save_path=base_dir, callbacks=[checkpoint_callback])
            model = Explainer(n_concepts=n_concepts, n_classes=n_classes, lr=0.01, explainer_hidden=[20, 20],
                              temperature=tau, l1=l1)

            start = time.time()
            trainer.fit(model, train_loader, val_loader)
            print(f"Gamma: {model.model[0].concept_mask}")
            model.freeze()
            model_results = trainer.test(model, test_dataloaders=test_loader)
            for j in range(n_classes):
                n_used_concepts = sum(model.model[0].concept_mask[j] > 0.5)
                print(f"Extracted concepts: {n_used_concepts}")
            results, f = model.explain_class(val_loader, train_loader, test_loader,
                                             topk_explanations=10,
                                             concept_names=concept_names)
            end = time.time() - start
            results['model_accuracy'] = model_results[0]['test_acc']
            results['extraction_time'] = end
            results['tau'] = tau
            results['lambda'] = l1

            results_list.append(results)

            results_df = pd.DataFrame(results_list)
            results_df.to_csv(os.path.join(base_dir, f'results_aware_vdem_l_{l1}_tau_{tau}.csv'))


print(results_list)
