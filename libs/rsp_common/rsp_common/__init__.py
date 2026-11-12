"""rsp_common — biblioteca compartida de la Robot Signs Platform.

Contiene únicamente lo que TODOS los microservicios necesitan para hablar el
mismo idioma: contratos de eventos (schemas), asuntos NATS, cliente de bus,
observabilidad y utilidades de salud. Nada de lógica de dominio aquí: cada
servicio es dueño de su dominio.
"""

__version__ = "0.1.0"
