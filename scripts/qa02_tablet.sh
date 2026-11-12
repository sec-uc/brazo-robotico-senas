#!/bin/bash
# QA-02 en el brazo real: 60 sesiones de texto desde Makia en la tablet; tiempos del envío en logcat (RSP_ARM).
T=${T:-172.18.78.192:5555}; S=$(dirname "$0"); OUT=$S/qa02_logcat.txt
WORDS="abajo agua arriba bien cuatro hola mal mama manana mano manzana mas%so%smenos mejor mesero miel mil moneda ocho un%sratito uno"
adb -s $T logcat -c
adb -s $T logcat -v epoch -s flutter > $OUT &
LP=$!
k=0
for rep in 1 2 3; do for w in $WORDS; do
  k=$((k+1)); echo "$k $w $(/usr/bin/python3 -c 'import time;print(int(time.time()*1000))')" >> $S/qa02_sesiones.txt
  adb -s $T shell input tap 812 1025; sleep 1.2
  # el campo conserva el texto anterior: fin de línea y 40 borrados antes de escribir
  adb -s $T shell input keyevent 123 $(printf '67 %.0s' $(seq 40)); sleep 0.3
  adb -s $T shell input text "$w"; sleep 0.3
  adb -s $T shell input keyevent 66
  # espera a que no lleguen más gestos durante 4 s (mínimo 4 s)
  sleep 4; last=$(grep -c RSP_ARM $OUT)
  while sleep 4; do n=$(grep -c RSP_ARM $OUT); [ "$n" = "$last" ] && break; last=$n; done
done; done
kill $LP; echo "sesiones=$k gestos=$(grep -c RSP_ARM $OUT)"
