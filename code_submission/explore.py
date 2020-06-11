import numpy as np
import gc
import collections
import torch
import time

class Explore:
    def __init__(self, info, model_space, data_space):
        """
        Training models and making predictions
        Parameters:
        ----------
        info: dict
            The eda infomation generated by AutoEDA
        model_space: ModelSpace
            Model space
        data_space: DataSpace
            Data space
        ----------
        """
        self.info = info
        self.model_space = model_space
        self.data_space = data_space

        self.models = self.model_space.get_models()
        self.model = None
        self.model_prior = self.model_space.model_prior
        self.model_idx = 0

        self.ensemble_threshold = self.info['ensemble_threshold']

        self.round_num = 1

        self.hist_info = {}
        self.pyg_data = None
        self.update_predict = True

    def explore_space(self):
        if self.model_idx == 0:
            print('Model Prior:', self.model_prior)
        self.explore_data_space()
        self.explore_model_space()
        val_score = self.model.trial(self.pyg_data, self.round_num)
        print('Model Name:', self.model.name, 'Round:', self.round_num, 'Val score:', val_score)
        
        self.update_model_hist(val_score)

    def explore_model_space(self):
        self.model = self.models[self.model_prior[self.model_idx]]
        self.model_idx += 1

    def explore_data_space(self):
        if self.data_space.update or self.pyg_data is None:
            self.pyg_data = self.data_space.get_data(round_num=self.round_num)
            self.data_space.update = False

    def update_model_hist(self, val_score):
        self.model.hist_score.append(val_score)
        if val_score > self.model.best_score:
            self.model.best_score = val_score
            self.update_predict = True
        else:
            self.update_predict = False

    def sort_model_prior(self):
        model_perform = collections.defaultdict(list)
        for name, info in self.hist_info.items():
            model_perform[name] = [e[0] for e in info]

        self.model_prior = sorted(self.model_prior, key=lambda x: np.mean(model_perform[x]), reverse=True)
        self.model_idx = 0
        self.round_num += 1

    def get_top_preds(self):
        models_name = self.hist_info.keys()
        top_score_and_preds_for_each_model = [sorted(self.hist_info[name], key=lambda e: e[0], reverse=True)[0] for name in models_name]

        models_name_sorted, models_score_and_preds_sorted = (list(i) for i in
                                                 zip(*sorted(zip(models_name, top_score_and_preds_for_each_model), key=lambda x: x[1][0], reverse=True)))

        models_score_sorted = [e[0] for e in models_score_and_preds_sorted]
        models_preds_sorted = [e[1] for e in models_score_and_preds_sorted]

        max_score = max(models_score_sorted)

        for i in range(len(models_score_sorted), 0, -1):
            top_num = i
            if models_score_sorted[i-1] + self.ensemble_threshold >= max_score:
                break

        top_score = np.array(models_score_sorted[:top_num])
        top_score = top_score + 50 * (top_score - top_score.mean())
        top_score = np.array([max(0.01, i) for i in top_score])
        weights = top_score / top_score.sum()
        print('Ensmble Models Weights:', weights)

        top_preds = []
        for i in range(top_num):
            name = models_name_sorted[i]
            rank = i + 1
            score = models_score_sorted[i]
            weight = weights[i]
            preds = models_preds_sorted[i]
            top_preds.append((name, rank, score, weight, preds))

        return top_preds

    def predict(self):
        if self.update_predict:
            preds = self.model.predict()
            self.model.best_preds = preds
            if self.model.name in self.hist_info:
                self.hist_info[self.model.name].append((self.model.best_score, self.model.best_preds))
            else:
                self.hist_info[self.model.name] = [(self.model.best_score, self.model.best_preds)]
            self.update_predict = False

        if self.model_idx >= len(self.model_prior):
            self.sort_model_prior()
            self.data_space.update = True

        preds = self.blending_predict().argmax(1).flatten()
        return preds

    def blending_predict(self):
        top_preds = self.get_top_preds()
        ensmble_models = []
        ensmble_val_scores = []
        ensmble_preds = 0
        for name, rank, score, weight, preds in top_preds:
            m = np.mean(preds)
            ensmble_models.append(name)
            ensmble_val_scores.append(score)
            ensmble_preds += weight * preds / m
        print('Ensmble Models Including:', ensmble_models)
        print('Ensmble Models Val Score:', ensmble_val_scores)
        return ensmble_preds