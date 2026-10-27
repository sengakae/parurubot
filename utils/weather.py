import os

import requests

weather_url = "http://api.openweathermap.org/data/2.5/weather?"


def fetch_weather(city: str) -> dict | None:
    """Current weather for a city, or None if the city isn't found. Blocking."""
    response = requests.get(
        weather_url, params={"appid": os.getenv("WEATHER_TOKEN"), "q": city}, timeout=10
    )
    data = response.json()
    if data.get("cod") != 200:
        return None

    description = data["weather"][0]
    return {
        "city": data.get("name", city),
        "country": data["sys"]["country"],
        "temp_c": round(data["main"]["temp"] - 273.15),
        "humidity": data["main"]["humidity"],
        "main": description["main"],
        "description": description["description"],
    }
