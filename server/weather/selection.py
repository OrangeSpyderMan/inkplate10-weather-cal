"""Forecast sampling without calendar or NumPy dependencies."""


def even_select(count, values):
    if count <= 0:
        return []
    if not values:
        raise ValueError("Cannot sample an empty forecast")
    if count == 1:
        return [values[0]]
    return [values[round(index * (len(values) - 1) / (count - 1))]
            for index in range(count)]
