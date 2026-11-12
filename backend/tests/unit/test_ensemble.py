import numpy as np

from backend.app.models.calibration.metrics import log_loss_score
from backend.app.models.ensemble.ensemble import blend_many, learn_ensemble_weights


def test_single_model_gets_full_weight():
    p = np.array([0.6, 0.3, 0.9])
    y = np.array([1, 0, 1])
    weights, loss = learn_ensemble_weights([p], y)
    assert weights == [1.0]
    assert loss == log_loss_score(y, p)


def test_weights_always_sum_to_one():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=200)
    p1 = np.clip(y * 0.6 + rng.normal(0, 0.2, 200) + 0.2, 0.01, 0.99)
    p2 = np.clip(y * 0.4 + rng.normal(0, 0.3, 200) + 0.3, 0.01, 0.99)
    p3 = np.clip(rng.uniform(0, 1, 200), 0.01, 0.99)  # ruido puro, deberia pesar ~0
    weights, _ = learn_ensemble_weights([p1, p2, p3], y, grid_step=0.1)
    assert len(weights) == 3
    assert abs(sum(weights) - 1.0) < 1e-9
    assert all(w >= 0 for w in weights)


def test_pure_noise_model_gets_low_or_no_weight():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, size=300)
    good = np.clip(np.where(y == 1, 0.85, 0.15) + rng.normal(0, 0.05, 300), 0.01, 0.99)
    noise = np.full(300, 0.5)
    weights, loss = learn_ensemble_weights([good, noise], y, grid_step=0.05)
    assert weights[0] >= weights[1]
    # el ensemble no deberia ser peor que el modelo bueno solo
    assert loss <= log_loss_score(y, good) + 1e-9


def test_blend_many_matches_manual_weighted_sum():
    p1 = np.array([0.2, 0.8])
    p2 = np.array([0.6, 0.4])
    blended = blend_many([p1, p2], [0.3, 0.7])
    expected = 0.3 * p1 + 0.7 * p2
    assert np.allclose(blended, expected)


def test_three_model_blend_improves_over_best_single_model_on_complementary_errors():
    # Tres modelos que se equivocan en subconjuntos DISTINTOS -- el ensemble
    # optimo deberia combinarlos en vez de quedarse con uno solo (motivacion
    # real de generalizar de 2 a N modelos: el root cause del punto 6 del
    # audit, un unico modelo "ganador" no basta cuando los errores no
    # estan correlacionados).
    y = np.array([1, 1, 1, 0, 0, 0, 1, 0])
    p_a = np.array([0.9, 0.9, 0.1, 0.1, 0.1, 0.1, 0.9, 0.1])  # falla en indice 2
    p_b = np.array([0.9, 0.1, 0.9, 0.1, 0.1, 0.9, 0.9, 0.1])  # falla en indices 1 y 5
    p_c = np.array([0.1, 0.9, 0.9, 0.9, 0.1, 0.1, 0.9, 0.1])  # falla en indices 0 y 3
    weights, ensemble_loss = learn_ensemble_weights([p_a, p_b, p_c], y, grid_step=0.05)
    best_single_loss = min(log_loss_score(y, p) for p in (p_a, p_b, p_c))
    assert ensemble_loss <= best_single_loss
