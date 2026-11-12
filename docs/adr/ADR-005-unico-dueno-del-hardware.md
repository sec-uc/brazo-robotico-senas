# ADR-005: Control de actuadores como único dueño del modelo del hardware

Estado: Aceptado · Fecha: 2026-09-12

## Contexto
Los límites de los 15 servos, el orden de ejecución y el protocolo serial estaban copiados en tres archivos de la app (P4), con riesgo de daño físico si divergen. El protocolo actual no tiene ACK.

## Decisión
`services/actuator-control/app/hardware.py` es la única definición de servos, límites, posiciones iniciales, orden de ejecución y trama serial. Todo ángulo recibido se recorta aquí. Los drivers (`simulator`, `serial`) implementan una interfaz común; una instancia por robot (sin queue group) y ejecución en serie con cola interna.

## Alternativas consideradas
- **Validar en Traducción a señas**: obligaría a ese servicio a conocer el hardware; rompería la sustitución del brazo.
- **ROS 2 desde el inicio**: deseable a futuro; se modela como un driver más.

## Consecuencias
+ Cambiar de brazo = un driver + una tabla.
− `done` con driver serial significa "comandos enviados": añadir ACK requiere modificar el firmware (trabajo futuro, registrado en riesgos).
