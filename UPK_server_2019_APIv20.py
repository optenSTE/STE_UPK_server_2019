import OptenFiberOpticDevices
import websockets
import logging
import asyncio
import json
import hyperion
import datetime

# Настроечные переменные

# address, port = '192.168.0.31', 7681  # адрес websocket-сервера
address, port = '192.168.1.216', 7681  # адрес websocket-сервера
index_of_reflection = 1.4682
speed_of_light = 299792458.0
output_measurements_order = {'T_degC': 1, 'Fav_N': 3, 'Fbend_N': 5, 'Ice_mm': 7}  # последовательность выдачи данных
DEFAULT_TIMEOUT = 10000

# Глобальные переменные
master_connection = None
instrument_description = dict()
h1 = None
active_channels = set()
devices = list()

# тайминги
asyncio_pause_sec = 0.01            # длительность паузы в корутинах, чтобы другие могли работать
x55_measurement_interval_sec = 0.1  # интервал выдачи измерений x55
data_averaging_interval_sec = 1     # интервал усреднения данных

# хранение длин волн
wls_buffer = dict()
wls_buffer['is_ready'] = True
wls_buffer['data'] = dict()

# хранение усредненных измерений
avg_buffer = dict()
avg_buffer['is_ready'] = True
avg_buffer['data'] = dict()

loop = asyncio.get_event_loop()
queue = asyncio.Queue(maxsize=5, loop=loop)


async def connection_handler(connection, path):
    global master_connection, instrument_description, devices, active_channels, x55_measurement_interval_sec
    logging.info('New connection {} - path {}'.format(connection.remote_address[:2], path))

    if not master_connection:
        master_connection = connection
    else:
        # master connection has already made, refuse this connection
        return False

    while True:
        await asyncio.sleep(asyncio_pause_sec)

        try:
            msg = await connection.recv()
        except websockets.exceptions.ConnectionClosed as e:
            # соединение закрыто, то нужно прекращать всю работу
            logging.info('connection closed, exception', e)

            # очищаем список соединений
            master_connection = None

            # have no instrument from now
            # instrument_description.clear()

            break

        logging.info("Received a message:\n %r" % msg)

        json_msg = dict()
        try:
            json_msg = json.loads(msg.replace("\'", "\""))
        except json.JSONDecodeError:
            logging.info('wrong JSON message has been refused')
            json_msg.clear()
            continue

        instrument_description = json_msg

        x55_measurement_interval_sec = 1.0 / instrument_description['LaserScanSpeed']

        # вытаскиваем информацию об устройствах
        devices = list()
        for device_description in instrument_description['devices']:

            # ToDo перенести это в класс ODTiT
            device = None
            try:
                device = OptenFiberOpticDevices.ODTiT(device_description['x55_channel'])
                device.id = device_description['ID']
                device.name = device_description['Name']
                device.channel = device_description['x55_channel']
                device.ctes = device_description['CTES']
                device.e = device_description['E']
                device.size = (device_description['Asize'], device_description['Bsize'])
                device.t_min = device_description['Tmin']
                device.t_max = device_description['Tmax']
                device.f_min = device_description['Fmin']
                device.f_max = device_description['Fmax']
                device.f_reserve = device_description['Freserve']
                device.span_rope_diameter = device_description['SpanRopeDiametr']
                device.span_len = device_description['SpanRopeLen']
                device.span_rope_density = device_description['SpanRopeDensity']
                device.span_rope_EJ = device_description['SpanRopeEJ']
                device.bend_sens = device_description['Bending_sensivity']
                device.time_of_flight = int(-2E9 * device_description['Distance'] * index_of_reflection / speed_of_light)

                device.sensors[0].id = device_description['Sensor4100']['ID']
                device.sensors[0].type = device_description['Sensor4100']['type']
                device.sensors[0].name = device_description['Sensor4100']['name']
                device.sensors[0].wl0 = device_description['Sensor4100']['WL0']
                device.sensors[0].t0 = device_description['Sensor4100']['T0']
                device.sensors[0].p_max = device_description['Sensor4100']['Pmax']
                device.sensors[0].p_min = device_description['Sensor4100']['Pmin']
                device.sensors[0].st = device_description['Sensor4100']['ST']

                device.sensors[1].id = device_description['Sensor3110_1']['ID']
                device.sensors[1].type = device_description['Sensor3110_1']['type']
                device.sensors[1].name = device_description['Sensor3110_1']['name']
                device.sensors[1].wl0 = device_description['Sensor3110_1']['WL0']
                device.sensors[1].t0 = device_description['Sensor3110_1']['T0']
                device.sensors[1].p_max = device_description['Sensor3110_1']['Pmax']
                device.sensors[1].p_min = device_description['Sensor3110_1']['Pmin']
                device.sensors[1].fg = device_description['Sensor3110_1']['FG']
                device.sensors[1].ctet = device_description['Sensor3110_1']['CTET']

                device.sensors[2].id = device_description['Sensor3110_2']['ID']
                device.sensors[2].type = device_description['Sensor3110_2']['type']
                device.sensors[2].name = device_description['Sensor3110_2']['name']
                device.sensors[2].wl0 = device_description['Sensor3110_2']['WL0']
                device.sensors[2].t0 = device_description['Sensor3110_2']['T0']
                device.sensors[2].p_max = device_description['Sensor3110_2']['Pmax']
                device.sensors[2].p_min = device_description['Sensor3110_2']['Pmin']
                device.sensors[2].fg = device_description['Sensor3110_2']['FG']
                device.sensors[2].ctet = device_description['Sensor3110_2']['CTET']

            except KeyError as e:
                return_error('JSON error - key ' + str(e) + ' did not find')

            devices.append(device)

        # находим все каналы, на которых есть решетки
        for device in devices:
            active_channels.add(int(device.channel))

        # проверяем готовность прибора

        instrument_ip = instrument_description['IP_address']
        if not isinstance(instrument_ip, str):
            instrument_ip = instrument_ip[0]

        """
        # соединяемся с x55
        h1 = hyperion.Hyperion(instrument_ip)
        while not h1:
            try:
                h1 = hyperion.Hyperion(instrument_ip)
            except hyperion.HyperionError as e:
                return_error(e)
                return None


        while not h1.is_ready:
            await asyncio.sleep(asyncio_pause_sec)
            pass

        logging.info('x55 is ready, sn', h1.serial_number)

        """
        # запускаем стриминг пиков
        await hyperion.HCommTCPPeaksStreamer(instrument_ip, loop, queue).stream_data()


def return_error(e):
    """ функция принимает все ошибки программы, передает их на сервер"""
    logging.info("Error %s" % e)
    return None


async def get_wls_from_x55_coroutine():
    """ получение длин волн от x55 c исходной частотой (складирование в буффер в памяти) """
    global wls_buffer, h1

    while True:

        peak_data = await queue.get()
        queue.task_done()
        if peak_data['data']:
            peaks_by_channel = dict()
            for channel in range(len(peak_data['data'].channel_slices)):
                wls = []
                for wl in peak_data['data'].channel_slices[channel]:
                    wls.append(wl)
                peaks_by_channel[channel+1] = wls

            measurement_time = peak_data['timestamp']

            wls_buffer['is_ready'] = False
            try:
                if measurement_time not in wls_buffer.setdefault('data', dict()):
                    wls_buffer['data'][measurement_time] = peaks_by_channel
            except KeyError as e:
                return_error('get_wls_from_x55_coroutine():' + str(e))
            finally:
                wls_buffer['is_ready'] = True

        else:
            # If the queue returns None, then the streamer has stopped.
            break

    # если нет информации об инструменте, то не можем получать данные
    while not instrument_description:
        await asyncio.sleep(asyncio_pause_sec)


async def averaging_measurements():
    """получение пересчет длин волн в измерения и усреднение"""
    global wls_buffer

    output_measurements_size = 2*len(output_measurements_order) + 1

    while True:
        await asyncio.sleep(asyncio_pause_sec)

        # ждем появления данных в буфере
        while len(wls_buffer.items()) < 2:
            await asyncio.sleep(x55_measurement_interval_sec)

        # ждем освобождения буфера
        while not wls_buffer['is_ready']:
            await asyncio.sleep(asyncio_pause_sec)

        # блокируем буфер (чтобы надежно с ним работать в многопоточном доступе)
        wls_buffer['is_ready'] = False
        try:
            for (measurement_time, peaks_by_channel) in wls_buffer['data'].items():

                # время усредненного блока, в которое попадает это измерение
                averaged_block_time = measurement_time - measurement_time % (1 / instrument_description['SampleRate'])

                devices_output = list()
                for device in devices:
                    # переводим пики в пикометры
                    wls_pm = list(map(lambda wl: wl * 1000, peaks_by_channel[device.channel]))

                    # переводим пики в пикометры, а также компенсируем все пики по расстоянию до текущего устройтва
                    # wls_pm = list(map(lambda wl: h1.shift_wavelength_by_offset(wl, device.time_of_flight) * 1000, peaks_by_channel[device.channel]))

                    # среди всех пиков ищем 3 подходящих для теукущего измерителя
                    wls = device.find_yours_wls(wls_pm, device.channel)

                    # если все три пика измерителя нашлись, то вычисляем тяжения и пр. Нет - вставляем пустышки
                    if wls:
                        device_output = device.get_tension_fav_ex(wls[1], wls[2], wls[0])
                    else:
                        device_output = device.get_tension_fav_ex(0, 0, 0, True)

                    device_output.setdefault('Time', measurement_time)

                    devices_output.append(device_output)

                # создаем запись с таким временем или добавляем в существующую
                cur_mean_block = avg_buffer['data'].setdefault(averaged_block_time, len(devices_output)*9*[0.0])

                # по всем измерениям текущего измерителя
                for device_num, cur_output in enumerate(devices_output):

                    # пустые измерения пропускаем
                    if not cur_output['T_degC']:
                        continue

                    # усредняем поля из списка
                    i = cur_mean_block[0 + device_num * output_measurements_size]
                    for name, index in output_measurements_order.items():
                        cur_mean_block[index + device_num * output_measurements_size] = (cur_mean_block[index + device_num * output_measurements_size]*i + cur_output[name]) \
                                                                                        / (i+1)
                    cur_mean_block[0 + device_num * output_measurements_size] += 1

            # измерения учтены, их можно удалять
            for cur_output_time in list(wls_buffer.keys()):
                if cur_output_time == 'is_ready':
                    continue
                del wls_buffer[cur_output_time]

        finally:
            wls_buffer['is_ready'] = True


async def send_avg_measurements():
    """получение пересчет длин волн в измерения и усреднение"""
    global avg_buffer

    while True:
        await asyncio.sleep(asyncio_pause_sec)

        # ждем появления данных в буфере
        while len(avg_buffer['data'].items()) < 2:
            await asyncio.sleep(data_averaging_interval_sec)

        # ждем освобождения буфера
        while not avg_buffer['is_ready']:
            await asyncio.sleep(asyncio_pause_sec)

        # блокируем буфер (чтобы надежно с ним работать в многопоточном доступе)
        avg_buffer['is_ready'] = False
        try:
            output_time = sorted(avg_buffer['data'].keys())[0]
            measurements = avg_buffer['data'][output_time]
            measurements.insert(0, output_time)

            data_arch_file_name = datetime.datetime.utcfromtimestamp(output_time).strftime('%Y%m%d%H.txt')
            with open(data_arch_file_name, 'a') as f:
                f.write("\t".join([str(x) for x in measurements])+'\n')

            # message preparing
            try:
                msg = json.dumps(measurements)
            except json.JSONDecodeError:
                continue

            if master_connection:
                logging.info('send message {} for connection {}'.format(msg, master_connection.remote_address[:2]))

                # is client still alive?
                try:
                    await master_connection.ping()
                except websockets.exceptions.ConnectionClosed:
                    continue

                # send data block
                try:
                    await master_connection.send(msg)
                except websockets.exceptions.ConnectionClosed:
                    continue

            del avg_buffer['data'][output_time]
        finally:
            avg_buffer['is_ready'] = True


if __name__ == "__main__":

    logging.basicConfig(format=u'%(filename)s[LINE:%(lineno)d]# %(levelname)-8s [%(asctime)s]  %(message)s', level=logging.DEBUG)  # , filename=u'UPK_server_2019.log')
    logging.info(u'Start file')

    # связь с сервером, получение описания прибора
    loop.run_until_complete(websockets.serve(connection_handler, address, port, ping_interval=None, ping_timeout=None))
    logging.info('Server {} has been started'.format((address, port)))

    # create the streamer object instance
    loop.create_task(get_wls_from_x55_coroutine())

    # получение длин волн от x55 c исходной частотой (складирование в буффер в памяти)
    # asyncio.async(get_wls_from_x55_coroutine())

    # получение пересчет длин волн в измерения и усреднение
    asyncio.async(averaging_measurements())

    # отправка усредненных измерений на сервер
    asyncio.async(send_avg_measurements())

    loop.run_forever()
