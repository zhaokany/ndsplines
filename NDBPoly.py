import numpy as np
from scipy import interpolate
import scipy_bspl

try:
    profile
except NameError:
    profile = lambda x: x

"""
TODOs:

implement periodic call

implement a least-squares constructor

"""
pinned = 2
clamped = 1
extrap = 0
periodic = -1

bc_map =  {clamped: "clamped", pinned: "natural", extrap: None, periodic: None}

@profile
def find_intervals(t, k, x, extrapolate=False, workspace=None):
    """
    Find an interval ell such that t[ell] <= x < t[ell+1].

    Parameters
    ----------
    t : ndarray, shape=(n+k+1,) dtype=np.float_
        knots
    k : int
        order of B-spline
    x : ndarray, shape=(s,) dtype=np.float_
        values to find the interval for
    extrapolate : bool
        whether to return the last or the first interval if xval
        is out of bounds.

    Returns
    -------
    ell : ndarray, shape=(s,) dtype=np.int_
        Suitable interval or -1 for each value in x

    Notes
    -----
    Similar to scipy\\interpolate\\_bspl.pyx find_Interval (inline)
    """
    if x.ndim != 1:
        raise ValueError("expected 1-dimensional x")

    s = x.size

    do_return = False
    if (not isinstance(workspace, np.ndarray) or 
            (workspace.dtype != np.int_) or
            (workspace.shape[0] < t.shape[0]) or
            (workspace.shape[1] < s)):
        workspace = np.empty((t.shape[0], s), dtype=np.int_)
        do_return = True
    
    ell = workspace[0,:s]

    # TODO: I am assuming memory is cheap and I don't get much for typing
    # the test array as bool_ vs int_
    test = workspace[1:t.shape[0],:s]

    ell[:] = -1
    test[:,:s] = (t[:-1,None] <= x[None,:]) & (x[None,:] < t[1:,None])
    
    if extrapolate:
        test[k,:s] = test[k,:s] | (x < t[k])
        test[-k-1,:s] = test[-k-1,:s] | (t[-k-1] <= x)

    # TODO: can we pre-allocate this? or is there a better way to implement
    # this whole function?
    where_test = np.where(test)
    ell[where_test[1]] = where_test[0]

    if do_return:
        return ell

@profile
def eval_bases(t, k, x, ell, nu=0, workspace=None):
    """
    Evaluate the k+1 non-zero spline basis functions for x

    Parameters
    ----------
    t : ndarray, shape=(n+k+1,) dtype=np.float_
        knots
    k : int
        order of B-spline.
    x : ndarray, shape=(s,) dtype=np.float_
        values to find the interval for
    ell : ndarray, shape=(s,) dtype=np.int_
        index such that t[ell] <= x < t[ell+1] for each x
    nu : int
        order of derivative to evaluate.

    Returns
    -------
    u : ndarray, shape=(k+1, s) dtype=np.float_
        the values of the non-zero spline basis functions evaluated at x

    Notes
    -----
    similar to scipy\\interpolate\\src\\__fitpack.h _deBoor_D (inline)
    """

    if x.ndim != 1:
        raise ValueError("expected 1-dimensional x")

    s = x.size

    do_return = False
    if (not isinstance(workspace, np.ndarray) or 
            (workspace.dtype != np.float_) or
            (workspace.shape[0] < 2*k+3) or
            (workspace.shape[1] < s)):
        workspace = np.empty((2*k+3, s), dtype=np.float_)
        do_return = True

    u = workspace[:k+1,:s]
    w = workspace[k+1:2*k+1,:s]
    bounds = workspace[2*k+1:2*k+3,:s]
    
    w[0,...] = 1.0
    for j in range(1, k-nu+1):

        u[0,:] = 0
        for n in range(1, j+1):
            index = ell+n
            bounds[0, :] = t[index]
            bounds[1, :] = t[index-j]
            neq_test = bounds[0, :] != bounds[1, :]
            u[n, ~neq_test] = 0.0

            # I'm not sure if using these views are cythonizable, but might
            # be faster in Python+Numpy?
            xb = bounds[0, neq_test]
            xa = bounds[1, neq_test]
            xx = x[neq_test]
            tau = w[n-1, neq_test]/(xb-xa)
            u[n-1, neq_test] += tau*(xb - xx)
            u[n, neq_test] = tau*(xx - xa)
            
        # w[:j] = u[:j]
        w[:] = u[:k].copy()

    for j in range(k-nu+1, k+1):
        u[0,:] = 0
        for n in range(1, j+1):
            index = ell+n
            bounds[0, :] = t[index]
            bounds[1, :] = t[index-j]
            neq_test = bounds[0, :] != bounds[1, :]
            u[n, ~neq_test] = 0.0 # __fitpack.h has h[m] = 0.0 here...

            xb = bounds[0, neq_test]
            xa = bounds[1, neq_test]
            xx = x[neq_test]
            tau = j*w[n-1, neq_test]/(xb-xa)
            u[n-1, neq_test] -= tau
            u[n, neq_test] = tau

        w[:] = u[:k].copy()

    if do_return:
        return u
    

class NDBPoly(object):
    def __init__(self, knots, coeffs, orders=3, periodic=False, extrapolate=True):
        """
        Parameters
        ----------
        knots : ndarray, shape=(ndim, n_1+orders[i-1], ..., n_ndim+orders[-1]), dtype=np.float_
        coeffs : ndarray, shape=(mdim, n_1, n_2, ..., n_ndim), dtype=np.float_
        orders : ndarray, shape=(ndim,), dtype=np.int_
        periodic : ndarray, shape=(ndim,), dtype=np.bool_
        extrapolate : ndarray, shape=(ndim,), dtype=np.bool_

        Notes
        -----
        TODO: maybe we don't need to include an extrapolate here? the user
        can restrict the inputs on __call__?
            
        """
        self.knots = knots
        self.coeffs = coeffs
        self.ndim = knots.shape[0] # dimension of knots
        self.mdim = coeffs.shape[0] # dimension of coefficeints
        self.orders = np.broadcast_to(orders, (self.ndim,))
        self.periodic = np.broadcast_to(periodic, (self.ndim,))
        self.extrapolate = np.broadcast_to(extrapolate, (self.ndim,2))

        self.u_ops = []
        self.input_op = list(i for i in range(self.ndim+1)) + [...,]
        self.output_op = [0,...]
        self.knots_vec = []
        self.cc_sel_base = np.meshgrid(*[np.arange(order+1) for order in self.orders], indexing='ij')
        self.eval_work = []
        self.ell_work = []
        self.cur_max_x_size = 1

        self.c_shape_base = (self.ndim,)+tuple(self.orders+1)

        self.uus = np.empty((self.ndim,np.max(self.orders)+1,self.cur_max_x_size,), dtype=np.float_)
        self.cc_sel = np.empty(self.c_shape_base + (self.cur_max_x_size,), dtype=np.int_)
        for i in range(self.ndim):
            self.u_ops.append([..., i+1,])
            knot_sel = ((i,) + (0,)*i + (slice(None,None),) + 
                (0,)*(self.ndim-i-1))

            #TODO: this is the only thing we need to keep in the constructor.
            self.knots_vec.append(
                np.ascontiguousarray(self.knots[knot_sel], dtype=np.float_)
            )

            self.ell_work.append(
                np.empty((self.cur_max_x_size,),dtype=np.int_))

            self.eval_work.append(
                np.empty((self.cur_max_x_size,2*self.orders[i]+3),dtype=np.float_))

    @profile
    def get_us_and_cc_sel(self, x, nus=0):
        """
        Parameters
        ----------
        x : ndarray, shape=(self.ndim, s) dtype=np.float_
        nus : ndarray, shape=(self.ndim,) dtype=np.int_
        """
        num_points = x.shape[-1]

        for i in range(self.ndim):
            t = self.knots_vec[i]
            k = self.orders[i]
            nu = nus[i]
            if self.periodic[i]:
                n = t.size - k - 1
                x[i,:] = t[k] + (x[i,:] - t[k]) % (t[n] - t[k])
                extrapolate_flag = False
                # find_intervals(t, k, x[i,:], False, self.ell_work[i])
            else:
                if not self.extrapolate[i,0]:
                    lt_sel = x[i,:] < t[k]
                    x[i,lt_sel] = t[k]
                if not self.extrapolate[i,1]:
                    gte_sel = t[-k-1] < x[i,:]
                    x[i,gte_sel] = t[-k-1]
                extrapolate_flag = True
                # find_intervals(t, k, x[i,:], True, self.ell_work[i])

            

            scipy_bspl.evaluate_spline(t, k, x[i,:], nu, extrapolate_flag, self.ell_work[i], self.eval_work[i],)
            ell = self.ell_work[i][:num_points]

            # eval_bases(t, k, x[i,:], ell, nu, self.eval_work[i])
            self.uus[i, :num_points, :k+1,] = self.eval_work[i][:num_points, :k+1]
            self.cc_sel[i, ..., :num_points] = self.cc_sel_base[i][..., None] + ell - k
        return self.cc_sel[..., :num_points], self.uus[..., :num_points]

    def check_workspace_shapes(self, x):
        if self.cur_max_x_size < x.shape[-1]:
            self.cur_max_x_size = x.shape[-1]
            for i in range(self.ndim):
                self.ell_work[i] = \
                    np.empty((self.cur_max_x_size,),dtype=np.int_)

                self.eval_work[i] = \
                    np.empty((self.cur_max_x_size,2*self.orders[i]+3,),dtype=np.float_)

            self.uus = np.empty((self.ndim, self.cur_max_x_size, np.max(self.orders)+1,), dtype=np.float_)
            self.cc_sel = np.empty(self.c_shape_base + (self.cur_max_x_size,), dtype=np.int_)

    def __call__(self, x, nus=0):
        """
        Parameters
        ----------
        x : ndarray, shape=(self.ndim,...) dtype=np.float_
        nus : ndarray, broadcastable to shape=(self.ndim,) dtype=np.int_
            
        """
        x_shape, x_ndim = x.shape, x.ndim
        x = np.ascontiguousarray(x.reshape((self.ndim, -1)), dtype=np.float_)
        nus = np.broadcast_to(nus, (self.ndim,))


        self.check_workspace_shapes(x)
        cc_sel, uus = self.get_us_and_cc_sel(x, nus)        
        cc_sel = (slice(None),) + tuple(cc_sel)
        ccs = self.coeffs[cc_sel]
        # TODO: why do the uus and u_ops go in the opposite order from what I 
        # expect? 

        # TODO: optimize einsum path, store it in case the shapes are the same
        # and/or write a memoization wrapper for the path optimizer
        y_out = np.einsum(ccs, self.input_op, 
            *[subarg for arg in zip(uus, self.u_ops) for subarg in arg], 
            self.output_op)
        y_out = y_out.reshape(
                    (self.mdim,) + x_shape[1:] if x_ndim!=1 else x_shape)
        return y_out


def make_interp_spline(x, y, bcs=0, orders=3):
    if isinstance(x, np.ndarray): # mesh
        if x.ndim == 1:
            ndim = 1
            x = x[None,...]
        else:
            ndim = x.ndim - 1

    elif not isinstance(x, str) and len(x): # vectors, or try
        ndim = len(x)
        x = np.meshgrid(x, indexing='ij')
        
    else:
        raise ValueError("Don't know how to interpret x")
    
    if y.ndim == ndim:
        # how can you tell if y needs a [None, ...] or [...] ?
        # same question as to whether ndim is y.ndim or y.ndim-1
        # I think this is the right answer. 
        y = y[None, ...]
    elif y.ndim == ndim+1:
        pass
    else:
        raise ValueError("incompatible dimension size")
    mdim = y.shape[0]
    # generally, x.shape = (ndim, n1, n2, ..., n_ndim)
    # and y.sahpe = (mdim, n1, n2, ..., n_ndim)
    bcs = np.broadcast_to(bcs, (ndim,2))
    orders = np.broadcast_to(orders, (ndim,))
    knot_shape = np.r_[x.shape]
    deriv_specs = np.asarray((bcs[:,:]>0),dtype=np.int)
    knot_shape[1:] = knot_shape[1:] + orders + 1 + deriv_specs.sum(axis=1)

    knots = np.zeros(knot_shape)
    coeffs = np.pad(y, np.r_[np.c_[0,0], deriv_specs], 'constant')

    for i in np.arange(ndim)+1:
        all_other_ax_shape = np.asarray(np.r_[coeffs.shape[1:i],
            y.shape[i+1:]], dtype=np.int)
        x_line_sel = ((i-1,) + (0,)*(i-1) + (slice(None,None),) +
            (0,)*(ndim-i))
        xp = x[x_line_sel]
        order = orders[i-1]
        for idx in np.ndindex(*all_other_ax_shape):
            offset_axes_remaining_sel = (tuple(idx[i-1:] + 
                deriv_specs[i:,0]))
            y_line_sel = ((Ellipsis,) + idx[:i-1] + 
                (slice(deriv_specs[i-1,0],-deriv_specs[i-1,0] or None),) +
                offset_axes_remaining_sel)
            coeff_line_sel = ((Ellipsis,) + idx[:i-1] + (slice(None,None),)
                + offset_axes_remaining_sel)
            line_spline = interpolate.make_interp_spline(xp,
                coeffs[y_line_sel].T,
                k = order,
                bc_type=(bc_map[(bcs[i-1,0])],
                         bc_map[(bcs[i-1,1])]),

            )
            coeffs[coeff_line_sel] = line_spline.c.T
        knots[i-1,...] = (line_spline.t[(None,)*(i-1) + 
            (slice(None),) + (None,)*(ndim-i)])
    return NDBPoly(knots, coeffs, orders, 
        np.all(bcs==periodic, axis=1),
        (bcs %2)==0)