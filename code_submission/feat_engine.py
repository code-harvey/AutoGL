import pandas as pd
import numpy as np
from sparsesvd import sparsesvd
import scipy
import time
import torch_geometric.transforms as T
from torch_geometric.nn import Node2Vec
import networkx as nx
import torch

class FeatEngine:
    """
    A tool box for generating node features.
    Feature type including: SVD / One Hot / Degree / Node2Vec / Adjacency Matrix .
    These features can be concatenated.
    Parameters:
    ----------
    info: dict
        The eda infomation generated by AutoEDA
    ----------
    """
    def __init__(self, info):
        self.info = info

    def fit_transform(self, data):
        if 'original' in self.info['feature_type']:
            print('Use Original Feature')
        if 'one_hot' in self.info['feature_type']:
            print('Use One Hot Feature')
            data['fea_table'] = self.generate_one_hot_feature(data)
        if 'svd' in self.info['feature_type']:
            print('Use SVD Feature')
            data['fea_table'] = self.generate_svd_feature(data, num_features=64)
        if 'degree' in self.info['feature_type']:
            print('Use Degree Feature')
            data['fea_table'] = self.generate_degree_feature(data)
        if 'node2vec' in self.info['feature_type']:
            print('Use Node2Vec Feature')
            data['fea_table'] = self.generate_node2vec_feature(data, epochs=20, num_features=64)
        if 'adj' in self.info['feature_type']:
            print('Use Adjacency Feature')
            data['fea_table'] = self.generate_adj_feature(data, use_weight=False)

    def generate_svd_feature(self, data, num_features=64):
        feat_df, edge_df = data['fea_table'], data['edge_file']
        adj_matrix = np.zeros((self.info['num_nodes'], self.info['num_nodes']))
        edges = edge_df.to_numpy(dtype=int)
        for edge in edges:
            adj_matrix[edge[0], edge[1]] = 1
        sparse_adj_matrix = scipy.sparse.csc_matrix(adj_matrix)
        ut, s, vt = sparsesvd(sparse_adj_matrix, num_features)
        svd_feats = pd.DataFrame(np.dot(ut.T, np.diag(s)))
        return pd.concat([feat_df, svd_feats], axis=1)

    def generate_adj_feature(self, data, use_weight=True):
        feat_df, edge_df = data['fea_table'], data['edge_file']
        adj_matrix = np.zeros((self.info['num_nodes'], self.info['num_nodes']))
        edges = edge_df.to_numpy(dtype=int)

        if use_weight:
            for edge in edges:
                adj_matrix[edge[0], edge[1]] = edge[2]
        else:
            for edge in edges:
                adj_matrix[edge[0], edge[1]] = 1
        
        adj_feats = pd.DataFrame(adj_matrix)
        return pd.concat([feat_df, adj_feats], axis=1)

    def generate_one_hot_feature(self, data):
        return pd.concat([data['fea_table'], pd.get_dummies(data['fea_table'].to_numpy().flatten())], axis=1)

    def generate_degree_feature(self, data):
        g = nx.DiGraph()
        edges = data['edge_file'].to_numpy().astype(int)
        g.add_weighted_edges_from(edges)

        degree_feat = np.zeros((self.info['num_nodes'], 2))
        for node_idx in range(self.info['num_nodes']):
            in_degree, out_degree = g.in_degree(node_idx), g.out_degree(node_idx)
            degree_feat[node_idx,0], degree_feat[node_idx,1] = in_degree, out_degree
            in_edges = g.in_edges(node_idx, data=True)
            out_edges = g.out_edges(node_idx, data=True)
            in_weights = [e[2]['weight'] for e in in_edges]
            out_weights = [e[2]['weight'] for e in out_edges]
            degree_feat[2] = in_degree - out_degree

        return pd.concat([data['fea_table'], pd.DataFrame(degree_feat)], axis=1)

    def generate_node2vec_feature(self, data, epochs=20, num_features=64):
        edge_index = data['edge_file'][['src_idx', 'dst_idx']].to_numpy()
        edge_index = sorted(edge_index, key=lambda d: d[0])
        edge_index = torch.tensor(edge_index, dtype=torch.long).transpose(0, 1)

        model = Node2Vec(edge_index, embedding_dim=num_features, walk_length=20,
                 context_size=10, walks_per_node=10, num_negative_samples=1, sparse=True).to('cuda')

        loader = model.loader(batch_size=128, shuffle=True, num_workers=4)
        optimizer = torch.optim.SparseAdam(model.parameters(), lr=0.01)

        def train():
            model.train()
            total_loss = 0
            for pos_rw, neg_rw in loader:
                optimizer.zero_grad()
                loss = model.loss(pos_rw.to('cuda'), neg_rw.to('cuda'))
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
            return total_loss / len(loader)

        for epoch in range(1, epochs+1):
            loss = train()
            print(f'Epoch: {epoch:02d}, Loss: {loss:.4f}')

        return pd.concat([data['fea_table'], pd.DataFrame(model().detach().cpu().numpy())], axis=1)
