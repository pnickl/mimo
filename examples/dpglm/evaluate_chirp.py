import os
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np
import numpy.random as npr

import mimo
from mimo import distributions, mixture
from mimo.util.text import progprint_xrange

import argparse

import matplotlib.pyplot as plt


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Evaluate DPGLM with a Stick-breaking prior')
    parser.add_argument('--datapath', help='path to dataset', default=os.path.abspath(mimo.__file__ + '/../../datasets'))
    parser.add_argument('--evalpath', help='path to evaluation', default=os.path.abspath(mimo.__file__ + '/../../evaluation/toy'))
    parser.add_argument('--nb_seeds', help='number of seeds', default=1, type=int)
    parser.add_argument('--prior', help='prior type', default='stick-breaking')
    parser.add_argument('--alpha', help='concentration parameter', default=5000, type=float)
    parser.add_argument('--nb_models', help='max number of models', default=50, type=int)
    parser.add_argument('--affine', help='affine functions', action='store_true', default=True)
    parser.add_argument('--no_affine', help='non-affine functions', dest='affine', action='store_false')
    parser.add_argument('--super_iters', help='interleaving Gibbs/VI iterations', default=1, type=int)
    parser.add_argument('--gibbs_iters', help='Gibbs iterations', default=50, type=int)
    parser.add_argument('--stochastic', help='use stochastic VI', action='store_true', default=False)
    parser.add_argument('--no_stochastic', help='do not use stochastic VI', dest='stochastic', action='store_false')
    parser.add_argument('--deterministic', help='use deterministic VI', action='store_true', default=True)
    parser.add_argument('--no_deterministic', help='do not use deterministic VI', dest='deterministic', action='store_false')
    parser.add_argument('--meanfield_iters', help='max VI iterations', default=250, type=int)
    parser.add_argument('--svi_iters', help='SVI iterations', default=500, type=int)
    parser.add_argument('--svi_stepsize', help='SVI step size', default=5e-4, type=float)
    parser.add_argument('--svi_batchsize', help='SVI batch size', default=20, type=int)
    parser.add_argument('--prediction', help='prediction w/ mode or average', default='average')
    parser.add_argument('--earlystop', help='stopping criterion for VI', default=1e-2, type=float)
    parser.add_argument('--verbose', help='show learning progress', action='store_true', default=True)
    parser.add_argument('--mute', help='show no output', dest='verbose', action='store_false')
    parser.add_argument('--nb_train', help='size of train dataset', default=500, type=int)
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

    # scale data
    from sklearn.decomposition import PCA
    input_scaler = PCA(n_components=1, whiten=True)
    target_scaler = PCA(n_components=1, whiten=True)

    input_scaler.fit(input)
    target_scaler.fit(target)

    input_scaled = input_scaler.transform(input)

    # prepare model
    input_dim, target_dim = 1, 1

    nb_params = input_dim
    if args.affine:
        nb_params += 1

    basis_prior = []
    models_prior = []

    # initialize Normal
    psi_niw = 1 * 1e-1
    kappa = 1e-2

    # initialize Matrix-Normal
    psi_mniw = 1e0
    V = 1e3 * np.eye(nb_params)

    for n in range(args.nb_models):
        basis_hypparams = dict(mu=np.zeros((input_dim, )),
                               psi=np.eye(input_dim) * psi_niw,
                               kappa=kappa, nu=input_dim + 1)

        aux = distributions.NormalInverseWishart(**basis_hypparams)
        basis_prior.append(aux)

        models_hypparams = dict(M=np.zeros((target_dim, nb_params)),
                                affine=args.affine, V=V,
                                nu=target_dim + 1,
                                psi=np.eye(target_dim) * psi_mniw)

        aux = distributions.MatrixNormalInverseWishart(**models_hypparams)
        models_prior.append(aux)

    # define gating
    if args.prior == 'stick-breaking':
        gating_hypparams = dict(K=args.nb_models, gammas=np.ones((args.nb_models,)), deltas=np.ones((args.nb_models,)) * args.alpha)
        gating_prior = distributions.StickBreaking(**gating_hypparams)

        dpglm = mixture.BayesianMixtureOfLinearGaussians(gating=distributions.BayesianCategoricalWithStickBreaking(gating_prior),
                                                         basis=[distributions.BayesianGaussian(basis_prior[i]) for i in range(args.nb_models)],
                                                         models=[distributions.BayesianLinearGaussian(models_prior[i]) for i in range(args.nb_models)])

    else:
        gating_hypparams = dict(K=args.nb_models, alphas=np.ones((args.nb_models,)) * args.alpha)
        gating_prior = distributions.Dirichlet(**gating_hypparams)

        dpglm = mixture.BayesianMixtureOfLinearGaussians(gating=distributions.BayesianCategoricalWithDirichlet(gating_prior),
                                                         basis=[distributions.BayesianGaussian(basis_prior[i]) for i in range(args.nb_models)],
                                                         models=[distributions.BayesianLinearGaussian(models_prior[i]) for i in range(args.nb_models)])

    anim = []

    split_size = int(nb_train / args.nb_splits)
    for n in range(args.nb_splits):
        print('Processing data split ' + str(n + 1) + ' out of ' + str(args.nb_splits))

        # # remove old data
        # try:
        #     dpglm.clear_data()
        # except IndexError:
        #     print('Model has no data')

        _input = input[n * split_size: (n + 1) * split_size, :]
        _target = target[n * split_size: (n + 1) * split_size, :]

        dpglm.add_data(y=_target, x=_input, whiten=True,
                       target_transform=target_scaler,
                       input_transform=input_scaler)

        # set posterior to prior
        import copy
        dpglm.gating.prior = copy.deepcopy(dpglm.gating.posterior)
        for i in range(dpglm.size):
            dpglm.basis[i].prior = copy.deepcopy(dpglm.basis[i].posterior)
            dpglm.models[i].prior = copy.deepcopy(dpglm.models[i].posterior)

        # train model
        for _ in range(args.super_iters):
            # Gibbs sampling
            if args.verbose:
                print("Gibbs Sampling")

            gibbs_iter = range(args.gibbs_iters) if not args.verbose\
                else progprint_xrange(args.gibbs_iters)

            for _ in gibbs_iter:
                dpglm.resample()

            if args.stochastic:
                # Stochastic meanfield VI
                if args.verbose:
                    print('Stochastic Variational Inference')

                svi_iter = range(args.gibbs_iters) if not args.verbose\
                    else progprint_xrange(args.svi_iters)

                batch_size = args.svi_batchsize
                prob = batch_size / float(len(_input))
                for _ in svi_iter:
                    minibatch = npr.permutation(len(_input))[:batch_size]
                    dpglm.meanfield_sgdstep(y=_target[minibatch, :], x=_input[minibatch, :],
                                            prob=prob, stepsize=args.svi_stepsize)
            if args.deterministic:
                # Meanfield VI
                if args.verbose:
                    print("Variational Inference")
                dpglm.meanfield_coordinate_descent(tol=args.earlystop,
                                                   maxiter=args.meanfield_iters,
                                                   progprint=args.verbose)

        # predict on all data
        sparse = False if (n + 1) < args.nb_splits else True
        mu, var, std, _ = dpglm.parallel_meanfield_prediction(x=input, sparse=sparse,
                                                              prediction=args.prediction)

        mu = np.hstack(mu)
        var = np.hstack(var)
        std = np.hstack(std)

        # plot prediction
        fig = plt.figure(figsize=(12, 4))
        plt.scatter(input, target, s=0.75, color='k')
        plt.axvspan(_input.min(),  _input.max(), facecolor='grey', alpha=0.1)
        plt.plot(input, mu, color='crimson')

        for c in [1., 2.]:
            plt.fill_between(input.flatten(), mu - c * std, mu + c * std, color=(0, 0, 1, 0.05))

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