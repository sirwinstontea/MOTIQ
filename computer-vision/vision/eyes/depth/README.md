# eyes/depth

Monocular distance for cameras with no depth sensor. `estimator.py` applies a pinhole model with known size priors to each detection, and `DepthSmoother` steadies the result over time.

The output is approximate. With a depth camera, `segmentation/measure.py` measures distance from real depth instead.
