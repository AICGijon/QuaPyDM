from sklearn.linear_model import LogisticRegression
import quapy as qp
from quapy.method.aggregative import OneVsAll
import quapy.functional as F
import numpy as np
import os
import pickle
import itertools
from joblib import Parallel, delayed
import settings
import argparse

parser = argparse.ArgumentParser(description='Run experiments for Tweeter Sentiment Quantification')
parser.add_argument('results', metavar='RESULT_PATH', type=str, help='path to the directory where to store the results')
parser.add_argument('svmperfpath', metavar='SVMPERF_PATH', type=str, help='path to the directory with svmperf')
args = parser.parse_args()


def quantification_models():
    def newLR():
        return LogisticRegression(max_iter=1000, solver='lbfgs', n_jobs=-1)
    __C_range = np.logspace(-4, 5, 10)
    lr_params = {'C': __C_range, 'class_weight': [None, 'balanced']}
    svmperf_params = {'C': __C_range}
    yield 'cc', qp.method.aggregative.CC(newLR()), lr_params
    yield 'acc', qp.method.aggregative.ACC(newLR()), lr_params
    yield 'pcc', qp.method.aggregative.PCC(newLR()), lr_params
    yield 'pacc', qp.method.aggregative.PACC(newLR()), lr_params
    yield 'sld', qp.method.aggregative.EMQ(newLR()), lr_params
    yield 'svmq', OneVsAll(qp.method.aggregative.SVMQ(args.svmperfpath)), svmperf_params
    yield 'svmkld', OneVsAll(qp.method.aggregative.SVMKLD(args.svmperfpath)), svmperf_params
    yield 'svmnkld', OneVsAll(qp.method.aggregative.SVMNKLD(args.svmperfpath)), svmperf_params
    yield 'svmmae', OneVsAll(qp.method.aggregative.SVMAE(args.svmperfpath)), svmperf_params
    yield 'svmmrae', OneVsAll(qp.method.aggregative.SVMRAE(args.svmperfpath)), svmperf_params

    #sld = qp.method.aggregative.EMQ(newLR())
    #yield 'paccsld', qp.method.aggregative.PACC(sld), lr_params

#     'mlpe': lambda learner: MaximumLikelihoodPrevalenceEstimation(),


def evaluate_experiment(true_prevalences, estim_prevalences):
    print('\nEvaluation Metrics:\n'+'='*22)
    for eval_measure in [qp.error.mae, qp.error.mrae]:
        err = eval_measure(true_prevalences, estim_prevalences)
        print(f'\t{eval_measure.__name__}={err:.4f}')
    print()


def evaluate_method_point_test(true_prev, estim_prev):
    print('\nPoint-Test evaluation:\n' + '=' * 22)
    print(f'true-prev={F.strprev(true_prev)}, estim-prev={F.strprev(estim_prev)}')
    for eval_measure in [qp.error.mae, qp.error.mrae]:
        err = eval_measure(true_prev, estim_prev)
        print(f'\t{eval_measure.__name__}={err:.4f}')


def result_path(dataset_name, model_name, optim_loss):
    return os.path.join(args.results, f'{dataset_name}-{model_name}-{optim_loss}.pkl')


def is_already_computed(dataset_name, model_name, optim_loss):
    if dataset_name=='semeval':
        check_datasets = ['semeval13', 'semeval14', 'semeval15']
    else:
        check_datasets = [dataset_name]
    return all(os.path.exists(result_path(name, model_name, optim_loss)) for name in check_datasets)


def save_results(dataset_name, model_name, optim_loss, *results):
    rpath = result_path(dataset_name, model_name, optim_loss)
    qp.util.create_parent_dir(rpath)
    with open(rpath, 'wb') as foo:
        pickle.dump(tuple(results), foo, pickle.HIGHEST_PROTOCOL)


def run(experiment):

    sample_size = 100
    qp.environ['SAMPLE_SIZE'] = sample_size

    optim_loss, dataset_name, (model_name, model, hyperparams) = experiment

    if is_already_computed(dataset_name, model_name, optim_loss=optim_loss):
        print(f'result for dataset={dataset_name} model={model_name} loss={optim_loss} already computed.')
        return
    elif (optim_loss=='mae' and model_name=='svmmrae') or (optim_loss=='mrae' and model_name=='svmmae'):
        print(f'skipping model={model_name} for optim_loss={optim_loss}')
        return
    else:
        print(f'running dataset={dataset_name} model={model_name} loss={optim_loss}')

    benchmark_devel = qp.datasets.fetch_twitter(dataset_name, for_model_selection=True, min_df=5, pickle=True)
    benchmark_devel.stats()

    # model selection (hyperparameter optimization for a quantification-oriented loss)
    model_selection = qp.model_selection.GridSearchQ(
        model,
        param_grid=hyperparams,
        sample_size=sample_size,
        n_prevpoints=21,
        n_repetitions=5,
        error=optim_loss,
        refit=False,
        timeout=60*60,
        verbose=True
    )
    model_selection.fit(benchmark_devel.training, benchmark_devel.test)
    model = model_selection.best_model()

    # model evaluation
    test_names = [dataset_name] if dataset_name != 'semeval' else ['semeval13', 'semeval14', 'semeval15']
    for test_no, test_name in enumerate(test_names):
        benchmark_eval = qp.datasets.fetch_twitter(test_name, for_model_selection=False, min_df=5, pickle=True)
        if test_no == 0:
            # fits the model only the first time
            model.fit(benchmark_eval.training)

        true_prevalences, estim_prevalences = qp.evaluation.artificial_sampling_prediction(
            model,
            test=benchmark_eval.test,
            sample_size=sample_size,
            n_prevpoints=21,
            n_repetitions=25
        )
        test_estim_prevalence = model.quantify(benchmark_eval.test.instances)
        test_true_prevalence = benchmark_eval.test.prevalence()

        evaluate_experiment(true_prevalences, estim_prevalences)
        evaluate_method_point_test(test_true_prevalence, test_estim_prevalence)
        save_results(test_name, model_name, optim_loss,
                     true_prevalences, estim_prevalences,
                     benchmark_eval.training.prevalence(), test_true_prevalence, test_estim_prevalence,
                     model_selection.best_params_)


if __name__ == '__main__':

    print(f'Result folder: {args.results}')
    np.random.seed(0)

    optim_losses = ['mae', 'mrae']
    datasets = qp.datasets.TWITTER_SENTIMENT_DATASETS_TRAIN
    models = quantification_models()

    results = Parallel(n_jobs=settings.N_JOBS)(
        delayed(run)(experiment) for experiment in itertools.product(optim_losses, datasets, models)
    )


