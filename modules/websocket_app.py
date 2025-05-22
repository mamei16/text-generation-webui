from typing import Dict, Any, Callable

import fastapi
import asyncio
from fastapi import WebSocket
import gradio
from gradio.routes import App

from modules import shared


original_routes_app = gradio.routes.App

class PatchRoutesApp:

    def __enter__(self):
        gradio.routes.App = WebSocketApp

    def __exit__(self, exc_type, exc_value, traceback):
        gradio.routes.App = original_routes_app



class WebSocketApp(App):
    @staticmethod
    def create_app(
        blocks: gradio.Blocks,
        app_kwargs: Dict[str, Any] | None = None,
        auth_dependency: Callable[[fastapi.Request], str | None] | None = None,
    ) -> App:
        app = App.create_app(blocks, app_kwargs, auth_dependency)

        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            shared.gradio["websocket"] = websocket
            try:
                # Keep the connection open
                while True:
                    await asyncio.sleep(1)
            except fastapi.WebSocketDisconnect:
                pass
        return app
