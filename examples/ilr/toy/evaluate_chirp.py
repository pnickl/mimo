import os
import argparse

os.environ["OMP_NUM_THREADS"] = "1"

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

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

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Evaluate ilr with a Stick-breaking prior')
    parser.add_argument('--datapath', help='path to dataset', default=os.path.abspath(mimo.__file__ + '/../../datasets'))
    parser.add_argument('--evalpath', help='path to evaluation', default=os.path.abspath(mimo.__file__ + '/../../evaluation/toy'))
    parser.add_argument('--nb_seeds', help='number of seeds', default=1, type=int)
    parser.add_argument('--prior', help='prior type', default='stick-breaking')
    parser.add_argument('--alpha', help='concentration parameter', default=50, type=float)
    parser.add_argument('--nb_models', help='max number of models', default=50, type=int)
    parser.add_argument('--affine', help='affine functions', action='store_true', default=True)
    parser.add_argument('--no_affine', help='non-affine functions', dest='affine', action='store_false')
    parser.add_argument('--super_iters', help='interleaving Gibbs/VI iterations', default=3, type=int)
    parser.add_argument('--gibbs_iters', help='Gibbs iterations', default=50, type=int)
    parser.add_argument('--stochastic', help='use stochastic VI', action='store_true', default=False)
    parser.add_argument('--no_stochastic', help='do not use stochastic VI', dest='stochastic', action='store_false')
    parser.add_argument('--deterministic', help='use deterministic VI', action='store_true', default=True)
    parser.add_argument('--no_deterministic', help='do not use deterministic VI', dest='deterministic', action='store_false')
    parser.add_argument('--meanfield_iters', help='max VI iterations', default=25, type=int)
    parser.add_argument('--svi_iters', help='SVI iterations', default=500, type=int)
    parser.add_argument('--svi_stepsize', help='SVI step size', default=0.9, type=float)
    parser.add_argument('--prediction', help='prediction w/ mode or average', default='average')
    parser.add_argument('--earlystop', help='stopping criterion for VI', default=1e-2, type=float)
    parser.add_argument('--verbose', help='show learning progress', action='store_true', default=True)
    parser.add_argument('--mute', help='show no output', dest='verbose', action='store_false')
    parser.add_argument('--nb_train', help='size of train dataset', default=1000, type=int)
    parser.add_argument('--nb_splits', help='number of dataset splits', default=25, type=int)
    parser.add_argument('--seed', help='choose seed', default=1337, type=int)

    args = parser.parse_args()

    np.random.seed(args.seed)

    # create Chrip data
    from scipy.signal import chirp

    nb_train = args.nb_train

    x = np.linspace(0, 5, nb_train)[:, None]
    y = chirp(x, f0=2.5, f1=1., t1=2.5, method='hyperbolic') + 0.25 * npr.randn(nb_train, 1)
    data = np.hstack((x, y))

    input, target = data[:, :1], data[:, 1:]

    # prepare model
    input_dim, target_dim = 1, 1

    nb_params = input_dim
    if args.affine:
        nb_params += 1

    basis_prior = []
    models_prior = []

    # initialize Normal
    psi_nw = 1e0
    kappa = 1e-2

    # initialize Matrix-Normal
    psi_mnw = 1e0
    K = 1e-2

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

    anim = []

    from sklearn.preprocessing import StandardScaler

    input_transform = StandardScaler()
    target_transform = StandardScaler()

    input_transform.fit(input)
    target_transform.fit(target)

    split_size = int(nb_train / args.nb_splits)
    for n in range(args.nb_splits):
        print('Processing data split ' + str(n + 1) + ' out of ' + str(args.nb_splits))

        # remove old data
        try:
            ilr.clear_data()
        except IndexError:
            print('Model has no data')

        _input = input[n * split_size: (n + 1) * split_size, :]
        _target = target[n * split_size: (n + 1) * split_size, :]

        ilr.add_data(y=_target, x=_input,
                     whiten=True,
                     input_transform=input_transform,
                     target_transform=target_transform)

        # set posterior to prior
        import copy

        ilr.gating.prior = copy.deepcopy(ilr.gating.posterior)
        for i in range(ilr.size):
            ilr.basis[i].prior = copy.deepcopy(ilr.basis[i].posterior)
            ilr.models[i].prior = copy.deepcopy(ilr.models[i].posterior)

        # Gibbs sampling
        ilr.resample(maxiter=args.gibbs_iters)

        # train model
        for _ in range(args.super_iters):
            if args.stochastic:
                # Stochastic meanfield VI
                ilr.meanfield_stochastic_descent(stepsize=args.svi_stepsize,
                                                 batchsize=split_size,
                                                 maxiter=1)
            if args.deterministic:
                # Meanfield VI
                ilr.meanfield_coordinate_descent(tol=args.earlystop,
                                                 maxiter=args.meanfield_iters,
                                                 progprint=args.verbose)

            # empirical Bayes
            ilr.gating.prior = ilr.gating.posterior
            for i in range(ilr.size):
                ilr.basis[i].prior = ilr.basis[i].posterior
                ilr.models[i].prior = ilr.models[i].posterior

        # predict on all data
        mu, var, std = ilr.meanfield_prediction(x=input, prediction=args.prediction)

        mu = np.hstack(mu)
        var = np.hstack(var)
        std = np.hstack(std)

        # plot prediction
        fig = plt.figure(figsize=(12, 4))
        plt.scatter(input, target, s=0.75, color='k')
        plt.axvspan(_input.min(), _input.max(), facecolor='grey', alpha=0.1)
        plt.plot(input, mu, color='crimson')

        for c in [1., 2.]:
            plt.fill_between(input.flatten(),
                             mu - c * std,
                             mu + c * std,
                             color=(0, 0, 1, 0.05))

        plt.ylim((-2.5, 2.5))

        anim.append(fig)

        plt.show()
        plt.pause(1)

        # set working directory
        dataset = 'chirp'
        try:
            os.chdir(args.evalpath + '/' + dataset)
        except FileNotFoundError:
            os.makedirs(args.evalpath + '/' + dataset, exist_ok=True)
            os.chdir(args.evalpath + '/' + dataset)

        # save tikz and pdf
        import tikzplotlib

        tikzplotlib.save(dataset + '_' + str(n) + '.tex')
        plt.savefig(dataset + '_' + str(n) + '.pdf')

    from moviepy.editor import VideoClip
    from moviepy.video.io.bindings import mplfig_to_npimage

    fps = 10


    def make_frame(t):
        idx = int(t * fps)
        return mplfig_to_npimage(anim[idx])


    # set working directory
    os.chdir(args.evalpath)
    dataset = 'chirp'
    path = os.path.join(str(dataset) + '/')

    animation = VideoClip(make_frame, duration=2.5)
    animation.write_gif(path + dataset + '.gif', fps=fps)
