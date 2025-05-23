from typing import Dict, Any, Callable, Optional
import logging

import fastapi
import asyncio
from fastapi import WebSocket, Depends, status, HTTPException
import gradio
from gradio.routes import App

from modules import shared


logger = logging.getLogger('text-generation-webui')

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

        async def ws_login_check(websocket: WebSocket) -> Optional[str]:
            token = websocket.cookies.get(
                f"access-token-{app.cookie_id}"
            ) or websocket.cookies.get(f"access-token-unsecure-{app.cookie_id}")
            return app.tokens.get(token) # token is returned to allow ws connection

        @app.get("/ws_login_check")
        @app.get("/ws_login_check/")
        def login_check(user: str = Depends(ws_login_check)):
            if (app.auth is None and app.auth_dependency is None) or user is not None:
                return
            logger.warning("Unauthorized WebSocket connection refused.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
            )

        @app.websocket("/ws", dependencies=[Depends(login_check)])
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            shared.gradio["websocket"] = websocket
            shared.gradio["main_loop"] = asyncio.get_running_loop()

            shared.gradio["processed_ws_message_count"] = 0
            async def read_from_socket(websocket: WebSocket):
                async for data in websocket.iter_text():
                    shared.gradio["processed_ws_message_count"] = int(data)

            asyncio.create_task(read_from_socket(websocket))
            try:
                # Keep the connection open
                while True:
                    await asyncio.sleep(1)
            except fastapi.WebSocketDisconnect:
                pass
        return app
