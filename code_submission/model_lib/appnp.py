import torch
import torch.nn.functional as F
from torch.nn import Linear
from torch_geometric.nn import APPNP
import copy
from sklearn.metrics import f1_score
from utils.tools import fix_seed, AverageMeter
from nni.hyperopt_tuner.hyperopt_tuner import HyperoptTuner
from torch_geometric.utils import dropout_adj
import random
fix_seed(1234)


class APPNPNet(torch.nn.Module):

    def __init__(self, info):
        super(APPNPNet, self).__init__()

        self.info = info

        self.best_score = 0
        self.hist_score = []

        self.best_preds = None
        self.current_round_best_preds = None
        self.best_valid_score = 0
        self.max_patience = 100
        self.max_epochs = 1600
        
        self.name = 'APPNP'

        self.hyperparameters = {
            'num_layers': self.info['num_layers'],
            'lr': 0.005,
            'K': 10,
            'alpha': 0.15,
            'dropedge_rate': self.info['dropedge_rate'],
            'dropout_rate': self.info['dropout_rate'],
            'hidden': self.info['init_hidden_size']
        }
        self.best_hp = None
        self.tuner = HyperoptTuner(algorithm_name='tpe', optimize_mode='maximize')
        search_space = {
                "dropedge_rate": {
                    "_type": "uniform",
                    "_value": [0, 1]
                },
                "dropout_rate": {
                    "_type": "uniform",
                    "_value": [0, 1]
                },
                "num_layers": {
                    "_type": "randint",
                    "_value": [2, 3]
                },
                "hidden": {
                    "_type": "quniform",
                    "_value": [4, 7, 1]
                },
                "lr":{
                    "_type": "choice",
                    "_value": [self.info['lr']]
                },
                'K' :{
                    "_type": "quniform",
                    "_value": [1, 6, 1]
                },
                'alpha':{
                    "_type": "uniform",
                    "_value": [0, 1] 
                }
            }
        self.tuner.update_search_space(search_space)

    def init_model(self, n_class, feature_num):
        hidden_size = int(2 ** self.hyperparameters['hidden'])
        K = int(self.hyperparameters['K'])
        self.lin1 = Linear(feature_num, hidden_size)
        self.lin2 = Linear(hidden_size, n_class)
        self.prop1 = APPNP(K=K, alpha=self.hyperparameters['alpha'])

        self.optimizer = torch.optim.Adam(self.parameters(), lr=self.hyperparameters['lr'], weight_decay=5e-4)

        self = self.to('cuda')

    def forward(self, data):
        x, edge_index, edge_weight = data.x, data.edge_index, data.edge_weight
        if self.hyperparameters['dropedge_rate'] is not None:
            edge_index, edge_weight = dropout_adj(edge_index, edge_weight, p=self.hyperparameters['dropedge_rate'],\
                 force_undirected=False, num_nodes=None, training=self.training)

        x = F.dropout(x, p=self.hyperparameters['dropout_rate'], training=self.training)
        x = F.relu(self.lin1(x))
        x = F.dropout(x, p=self.hyperparameters['dropout_rate'], training=self.training)
        x = self.lin2(x)
        x = self.prop1(x, edge_index,edge_weight)
        return x

    def trial(self, data, round_num):
        n_class, feature_num = self.info['n_class'], data.x.shape[1]
        if round_num >= 2:
            self.hyperparameters = self.tuner.generate_parameters(round_num-1)
        print(self.hyperparameters)    
           
        while True:
            try:
                self.init_model(n_class, feature_num)
                best_valid_score = self.train_valid(data, round_num)
                if round_num > 1:
                    self.tuner.receive_trial_result(round_num-1,self.hyperparameters,val_score)
                if val_score > self.best_score:
                    self.best_hp = copy.deepcopy(self.hyperparameters)
                break
            except RuntimeError as e:
                print(self.name,e, 'OOM with Hidden Size', self.hyperparameters['hidden'])
                if round_num > 1:
                    self.tuner.receive_trial_result(round_num-1,self.hyperparameters,0)
                return 0
        print("Best Hyperpameters of", self.name, self.best_hp)
        return val_score

    def train_valid(self, data, round_num):
        y, train_mask, valid_mask, test_mask, label_weights = data.y, data.train_mask, data.valid_mask, data.test_mask, data.label_weights

        patience = self.max_patience
        best_valid_score = 0
        valid_acc_meter = AverageMeter()
        for epoch in range(self.max_epochs):

            # train
            self.train()
            self.optimizer.zero_grad()
            preds = self.forward(data)
            loss = F.cross_entropy(preds[train_mask], y[train_mask], label_weights)
            loss.backward()
            self.optimizer.step()

            # valid
            self.eval()
            with torch.no_grad():
                preds = F.softmax(self.forward(data), dim=-1)
                valid_preds, test_preds = preds[valid_mask], preds[test_mask]
                valid_score = f1_score(y[valid_mask].cpu(), valid_preds.max(1)[1].flatten().cpu(), average='micro')

            valid_acc_meter.update(valid_score)

            # patience
            if valid_acc_meter.avg > best_valid_score:
                best_valid_score = valid_acc_meter.avg
                self.current_round_best_preds = test_preds
                patience = self.max_patience
            else:
                patience -= 1

            if patience == 0:
                break

        return best_valid_score

    def predict(self):
        return self.current_round_best_preds.cpu().numpy()

    def __repr__(self):
        return self.__class__.__name__