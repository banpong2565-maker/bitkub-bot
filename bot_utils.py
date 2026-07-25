import logging

logger = logging.getLogger('bitkub_bot')
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

def get_capped_sell_amount(initial_amount: float, available_amount: float, percentage: float) -> float:
    """Return the sell amount capped by the available balance.
    - Logs a warning if either amount is non‑positive.
    - Logs an info message when the desired amount is capped.
    """
    if initial_amount <= 0:
        logger.warning("initial_amount <= 0 (%.6f); selling 0", initial_amount)
        return 0.0
    if available_amount <= 0:
        logger.warning("available amount <= 0 (%.6f); selling 0", available_amount)
        return 0.0
    desired = initial_amount * percentage
    if desired > available_amount:
        logger.info(
            "Capping sell amount: desired %.6f > available %.6f; using available",
            desired,
            available_amount,
        )
        return available_amount
    return desired
