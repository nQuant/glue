from __future__ import absolute_import, division, print_function

import numpy as np

from glue.core import Subset
from glue.utils import defer_draw, datetime64_to_mpl

from glue.viewers.histogram.state import HistogramLayerState
from glue.viewers.histogram.python_export import python_export_histogram_layer
from glue.viewers.matplotlib.layer_artist import MatplotlibLayerArtist
from glue.core.exceptions import IncompatibleAttribute


class HistogramLayerArtist(MatplotlibLayerArtist):

    _layer_state_cls = HistogramLayerState
    _python_exporter = python_export_histogram_layer

    def __init__(self, axes, viewer_state, layer_state=None, layer=None):

        super(HistogramLayerArtist, self).__init__(axes, viewer_state,
                                                   layer_state=layer_state, layer=layer)

        # Watch for changes in the viewer state which would require the
        # layers to be redrawn
        self._viewer_state.add_global_callback(self._update_histogram)
        self.state.add_global_callback(self._update_histogram)

        self.reset_cache()

    def remove(self):
        super(HistogramLayerArtist, self).remove()
        self.mpl_hist_unscaled = np.array([])
        self.mpl_hist_edges = np.array([])

    def reset_cache(self):
        self._last_viewer_state = {}
        self._last_layer_state = {}

    @defer_draw
    def _calculate_histogram(self):

        self.remove()

        if isinstance(self.layer, Subset):
            data = self.layer.data
            subset_state = self.layer.subset_state
        else:
            data = self.layer
            subset_state = None

        try:
            x_comp = data.get_component(self._viewer_state.x_att)
        except AttributeError:
            return
        except (IncompatibleAttribute, IndexError):
            self.disable_invalid_attributes(self._viewer_state.x_att)
            return
        else:
            self.enable()

        range = sorted((self._viewer_state.hist_x_min, self._viewer_state.hist_x_max))

        hist_values = data.compute_histogram([self._viewer_state.x_att],
                                             range=[range],
                                             bins=[self._viewer_state.hist_n_bin],
                                             log=[self._viewer_state.x_log],
                                             subset_state=subset_state)

        if isinstance(range[0], np.datetime64):
            range = [datetime64_to_mpl(range[0]), datetime64_to_mpl(range[1])]

        if self._viewer_state.x_log:
            hist_edges = np.logspace(np.log10(range[0]), np.log10(range[1]), self._viewer_state.hist_n_bin + 1)
        else:
            hist_edges = np.linspace(range[0], range[1], self._viewer_state.hist_n_bin + 1)

        self.mpl_artists = self.axes.bar(hist_edges[:-1], hist_values, align='edge', width=np.diff(hist_edges)).get_children()

        self.mpl_hist_unscaled = hist_values
        self.mpl_hist_edges = hist_edges

    @defer_draw
    def _scale_histogram(self):

        if self.mpl_hist_edges.size == 0 or self.mpl_hist_unscaled.sum() == 0:
            return

        self.mpl_hist = self.mpl_hist_unscaled.astype(np.float)
        dx = self.mpl_hist_edges[1] - self.mpl_hist_edges[0]

        if self._viewer_state.cumulative:
            self.mpl_hist = self.mpl_hist.cumsum()
            if self._viewer_state.normalize:
                self.mpl_hist /= self.mpl_hist.max()
        elif self._viewer_state.normalize:
            self.mpl_hist /= (self.mpl_hist.sum() * dx)

        bottom = 0 if not self._viewer_state.y_log else 1e-100

        for mpl_artist, y in zip(self.mpl_artists, self.mpl_hist):
            mpl_artist.set_height(y)
            x, y = mpl_artist.get_xy()
            mpl_artist.set_xy((x, bottom))

        # We have to do the following to make sure that we reset the y_max as
        # needed. We can't simply reset based on the maximum for this layer
        # because other layers might have other values, and we also can't do:
        #
        #   self._viewer_state.y_max = max(self._viewer_state.y_max, result[0].max())
        #
        # because this would never allow y_max to get smaller.

        self.state._y_max = self.mpl_hist.max()
        if self._viewer_state.y_log:
            self.state._y_max *= 2
        else:
            self.state._y_max *= 1.2

        if self._viewer_state.y_log:
            self.state._y_min = self.mpl_hist[self.mpl_hist > 0].min() / 10
        else:
            self.state._y_min = 0

        largest_y_max = max(getattr(layer, '_y_max', 0) for layer in self._viewer_state.layers)
        if largest_y_max != self._viewer_state.y_max:
            self._viewer_state.y_max = largest_y_max

        smallest_y_min = min(getattr(layer, '_y_min', np.inf) for layer in self._viewer_state.layers)
        if smallest_y_min != self._viewer_state.y_min:
            self._viewer_state.y_min = smallest_y_min

        self.redraw()

    @defer_draw
    def _update_visual_attributes(self):

        if not self.enabled:
            return

        for mpl_artist in self.mpl_artists:
            mpl_artist.set_visible(self.state.visible)
            mpl_artist.set_zorder(self.state.zorder)
            mpl_artist.set_edgecolor('none')
            mpl_artist.set_facecolor(self.state.color)
            mpl_artist.set_alpha(self.state.alpha)

        self.redraw()

    def _update_histogram(self, force=False, **kwargs):

        if (self._viewer_state.hist_x_min is None or
                self._viewer_state.hist_x_max is None or
                self._viewer_state.hist_n_bin is None or
                self._viewer_state.x_att is None or
                self.state.layer is None):
            return

        # Figure out which attributes are different from before. Ideally we shouldn't
        # need this but currently this method is called multiple times if an
        # attribute is changed due to x_att changing then hist_x_min, hist_x_max, etc.
        # If we can solve this so that _update_histogram is really only called once
        # then we could consider simplifying this. Until then, we manually keep track
        # of which properties have changed.

        changed = set()

        if not force:

            for key, value in self._viewer_state.as_dict().items():
                if value != self._last_viewer_state.get(key, None):
                    changed.add(key)

            for key, value in self.state.as_dict().items():
                if value != self._last_layer_state.get(key, None):
                    changed.add(key)

        self._last_viewer_state.update(self._viewer_state.as_dict())
        self._last_layer_state.update(self.state.as_dict())

        if force or any(prop in changed for prop in ('layer', 'x_att', 'hist_x_min', 'hist_x_max', 'hist_n_bin', 'x_log')):
            self._calculate_histogram()
            force = True  # make sure scaling and visual attributes are updated

        if force or any(prop in changed for prop in ('y_log', 'normalize', 'cumulative')):
            self._scale_histogram()

        if force or any(prop in changed for prop in ('alpha', 'color', 'zorder', 'visible')):
            self._update_visual_attributes()

    @defer_draw
    def update(self):
        self._update_histogram(force=True)
        self.redraw()
