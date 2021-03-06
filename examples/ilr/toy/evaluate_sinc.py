import os
import argparse

os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np
import numpy.random as npr

import mimo
from mimo.distributions import NormalWishart
from mimo.distributions import MatrixNormalWishart
from mimo.distributions import GaussianWithNormalWishart
from mimo.distributions import LinearGaussianWithMatrixNormalWishart

from mimo.distributions import StickBreaking
from mimo.distributions import Dirichlet
from mimo.distributions import CategoricalWithDirichlet
from mimo.distributions import CategoricalWithStickBreaking

from mimo.mixtures import BayesianMixtureOfLinearGaussians

import matplotlib.pyplot as plt
from tqdm import tqdm

import pathos
from pathos.pools import _ProcessPool as Pool

nb_cores = pathos.multiprocessing.cpu_count()


def _job(kwargs):
    args = kwargs.pop('arguments')
    seed = kwargs.pop('seed')

    input = kwargs.pop('train_input')
    target = kwargs.pop('train_target')

    input_dim = input.shape[-1]
    target_dim = target.shape[-1]

    basis_prior = []
    models_prior = []

    # set random seed
    np.random.seed(seed)

    nb_params = input_dim
    if args.affine:
        nb_params += 1

    # initialize Normal
    psi_nw = 1e2
    kappa = 1e-2

    # initialize Matrix-Normal
    psi_mnw = 1e0
    K = 1e-3

    for n in range(args.nb_models):
        basis_hypparams = dict(mu=np.zeros((input_dim,)),
                               psi=np.eye(input_dim) * psi_nw,
                               kappa=kappa, nu=input_dim + 1)

        aux = NormalWishart(**basis_hypparams)
        basis_prior.append(aux)

        models_hypparams = dict(M=np.zeros((target_dim, nb_params)),
                                K=K * np.eye(nb_params), nu=target_dim + 1,
                                psi=np.eye(target_dim) * psi_mnw)

        aux = MatrixNormalWishart(**models_hypparams)
        models_prior.append(aux)

    # define gating
    if args.prior == 'stick-breaking':
        gating_hypparams = dict(K=args.nb_models, gammas=np.ones((args.nb_models,)),
                                deltas=np.ones((args.nb_models,)) * args.alpha)
        gating_prior = StickBreaking(**gating_hypparams)

        ilr = BayesianMixtureOfLinearGaussians(gating=CategoricalWithStickBreaking(gating_prior),
                                               basis=[GaussianWithNormalWishart(basis_prior[i])
                                                      for i in range(args.nb_models)],
                                               models=[LinearGaussianWithMatrixNormalWishart(models_prior[i], affine=args.affine)
                                                       for i in range(args.nb_models)])

    else:
        gating_hypparams = dict(K=args.nb_models, alphas=np.ones((args.nb_models,)) * args.alpha)
        gating_prior = Dirichlet(**gating_hypparams)

        ilr = BayesianMixtureOfLinearGaussians(gating=CategoricalWithDirichlet(gating_prior),
                                               basis=[GaussianWithNormalWishart(basis_prior[i])
                                                      for i in range(args.nb_models)],
                                               models=[LinearGaussianWithMatrixNormalWishart(models_prior[i], affine=args.affine)
                                                       for i in range(args.nb_models)])
    ilr.add_data(target, input, whiten=True)

    # Gibbs sampling
    ilr.resample(maxiter=args.gibbs_iters,
                 progprint=args.verbose)

    for _ in range(args.super_iters):
        if args.stochastic:
            # Stochastic meanfield VI
            ilr.meanfield_stochastic_descent(maxiter=args.svi_iters,
                                             stepsize=args.svi_stepsize,
                                             batchsize=args.svi_batchsize)
        if args.deterministic:
            # Meanfield VI
            ilr.meanfield_coordinate_descent(tol=args.earlystop,
                                             maxiter=args.meanfield_iters,
                                             progprint=args.verbose)

        ilr.gating.prior = ilr.gating.posterior
        for i in range(ilr.size):
            ilr.basis[i].prior = ilr.basis[i].posterior
            ilr.models[i].prior = ilr.models[i].posterior

    return ilr


def parallel_dpglm_inference(nb_jobs=50, **kwargs):
    kwargs_list = []
    for n in range(nb_jobs):
        _kwargs = {'seed': kwargs['arguments'].seed,
                   'train_input': kwargs['train_input'][n],
                   'train_target': kwargs['train_target'][n],
                   'arguments': kwargs['arguments']}
        kwargs_list.append(_kwargs)

    with Pool(processes=min(nb_jobs, nb_cores),
              initializer=tqdm.set_lock,
              initargs=(tqdm.get_lock(),)) as p:
        res = p.map(_job, kwargs_list)

    return res


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Evaluate ilr with a Stick-breaking prior')
    parser.add_argument('--datapath', help='path to dataset', default=os.path.abspath(mimo.__file__ + '/../../datasets'))
    parser.add_argument('--evalpath', help='path to evaluation', default=os.path.abspath(mimo.__file__ + '/../../evaluation/toy'))
    parser.add_argument('--nb_seeds', help='number of seeds', default=24, type=int)
    parser.add_argument('--prior', help='prior type', default='stick-breaking')
    parser.add_argument('--alpha', help='concentration parameter', default=100, type=float)
    parser.add_argument('--nb_models', help='max number of models', default=100, type=int)
    parser.add_argument('--affine', help='affine functions', action='store_true', default=True)
    parser.add_argument('--no_affine', help='non-affine functions', dest='affine', action='store_false')
    parser.add_argument('--super_iters', help='interleaving Gibbs/VI iterations', default=3, type=int)
    parser.add_argument('--gibbs_iters', help='Gibbs iterations', default=25, type=int)
    parser.add_argument('--stochastic', help='use stochastic VI', action='store_true', default=False)
    parser.add_argument('--no_stochastic', help='do not use stochastic VI', dest='stochastic', action='store_false')
    parser.add_argument('--deterministic', help='use deterministic VI', action='store_true', default=True)
    parser.add_argument('--no_deterministic', help='do not use deterministic VI', dest='deterministic', action='store_false')
    parser.add_argument('--meanfield_iters', help='max VI iterations', default=2500, type=int)
    parser.add_argument('--svi_iters', help='SVI iterations', default=500, type=int)
    parser.add_argument('--svi_stepsize', help='SVI step size', default=5e-4, type=float)
    parser.add_argument('--svi_batchsize', help='SVI batch size', default=512, type=int)
    parser.add_argument('--prediction', help='prediction w/ mode or average', default='average')
    parser.add_argument('--earlystop', help='stopping criterion for VI', default=1e-2, type=float)
    parser.add_argument('--verbose', help='show learning progress', action='store_true', default=True)
    parser.add_argument('--mute', help='show no output', dest='verbose', action='store_false')
    parser.add_argument('--seed', help='choose seed', default=1337, type=int)

    args = parser.parse_args()

    from sklearn.metrics import explained_variance_score
    from sklearn.metrics import mean_squared_error
    from sklearn.metrics import r2_score

    import tikzplotlib

    np.random.seed(args.seed)

    # sample dataset
    nb_samples = 10000
    input = np.linspace(-10., 10., nb_samples).reshape(nb_samples, 1)
    noise = lambda x: 0.05 + 0.2 * (1. + np.sin(2. * x)) / (1. + np.exp(-0.2 * x))
    target = np.sinc(input) + noise(input) * np.random.randn(len(input), 1)
    mean = np.sinc(input)

    data = np.hstack((input, target))

    # shuffle data
    from sklearn.utils import shuffle

    data = shuffle(data)

    # split to nb_seeds train datasets
    from sklearn.model_selection import ShuffleSplit

    spliter = ShuffleSplit(n_splits=args.nb_seeds, test_size=0.2)

    train_inputs, train_targets = [], []
    for train_index, _ in spliter.split(data):
        train_inputs.append(data[train_index, :1])
        train_targets.append(data[train_index, 1:])

    # train
    ilrs = parallel_dpglm_inference(nb_jobs=args.nb_seeds,
                                    train_input=train_inputs,
                                    train_target=train_targets,
                                    arguments=args)

    # Evaluation over multiple seeds to get confidence
    mu, std, = [], []
    evar, mse, smse = [], [], []
    for ilr in ilrs:
        _mu, _var, _std = ilr.meanfield_prediction(input, prediction=args.prediction)

        _mse = mean_squared_error(target, _mu)
        _evar = explained_variance_score(target, _mu, multioutput='variance_weighted')
        _smse = 1. - r2_score(target, _mu, multioutput='variance_weighted')

        print('EVAR:', _evar, 'MSE:', _mse, 'SMSE:', _smse,
              'Compnents:', len(ilr.used_labels))

        mu.append(_mu)
        std.append(_std)
        evar.append(_evar)
        mse.append(_mse)
        smse.append(_smse)

    mu = np.asarray(mu).squeeze()
    std = np.asarray(std).squeeze()

    # calcule means and confidence intervals
    mu_avg, mu_std = np.mean(mu, axis=0), np.std(mu, axis=0)
    std_avg, std_std = np.mean(std, axis=0), np.std(std, axis=0)

    # plot mean and standard deviation of mean estimation
    w, h = plt.figaspect(0.67)
    fig, axes = plt.subplots(2, 1, figsize=(w, h))

    # plot data and prediction
    axes[0].plot(input, mean, 'k--', zorder=10)
    axes[0].scatter(input, target, s=0.75, facecolors='none', edgecolors='grey', zorder=1)
    axes[0].plot(input, mu_avg, '-r', zorder=5)
    for c in [1., 2.]:
        axes[0].fill_between(input.flatten(),
                             mu_avg - c * mu_std,
                             mu_avg + c * mu_std,
                             edgecolor=(0, 0, 1, 0.1), facecolor=(0, 0, 1, 0.1), zorder=1)

    # plot mean and standard deviation of data generation / estimated noise level
    axes[1].plot(input, noise(input), 'k--', zorder=10)
    axes[1].plot(input, std_avg, '-r', zorder=5)
    for c in [1., 2.]:
        axes[1].fill_between(input.flatten(),
                             std_avg - c * std_std,
                             std_avg + c * std_std,
                             edgecolor=(0, 0, 1, 0.1), facecolor=(0, 0, 1, 0.1), zorder=1)
    plt.tight_layout()

    # set working directory
    dataset = 'sinc'
    try:
        os.chdir(args.evalpath + '/' + dataset)
    except FileNotFoundError:
        os.makedirs(args.evalpath + '/' + dataset, exist_ok=True)
        os.chdir(args.evalpath + '/' + dataset)

    # save figs
    import tikzplotlib

    tikzplotlib.save(dataset + '_mean.tex')
    plt.savefig(dataset + '_mean.pdf')

    # plt.show()

    # show on learned example
    choice = np.random.choice(args.nb_seeds, 1)[0]
    w, h = plt.figaspect(0.67)
    fig, axes = plt.subplots(2, 1, figsize=(w, h))

    # plot data and prediction
    axes[0].plot(input, mean, 'k--', zorder=10)
    axes[0].plot(input, mean + 2 * noise(input), 'g--', zorder=10)
    axes[0].plot(input, mean - 2 * noise(input), 'g--', zorder=10)
    axes[0].scatter(input, target, s=0.75, facecolors='none', edgecolors='grey', zorder=1)

    axes[0].plot(input, mu[choice], '-r', zorder=5)
    axes[0].plot(input, mu[choice] + 2 * std[choice], '-b', zorder=5)
    axes[0].plot(input, mu[choice] - 2 * std[choice], '-b', zorder=5)

    # plot gaussian activations
    activations = ilrs[choice].meanfield_predictive_activation(input)
    axes[1].plot(input, activations)

    # save figs
    import tikzplotlib

    tikzplotlib.save(dataset + '_example.tex')
    plt.savefig(dataset + '_example.pdf')

    # plt.show()
