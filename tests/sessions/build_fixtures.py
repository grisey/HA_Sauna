"""Materialize open-loop session inputs from bundled measured climate shapes.

This is fixture preparation, not a product simulation: it imports no application
code and never adapts a curve or an oracle to application output. All four climate
channels are joined in temperature and water-vapour density before RH conversion.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from fixture_configuration import catalog_configuration, configuration_reference

HERE = Path(__file__).resolve().parents[1] / 'fixtures' / 'scenarios'
SOURCE = json.loads((HERE / 'source_curves.json').read_text())
INPUTS = json.loads((HERE / 'construction.json').read_text())
ROLES = SOURCE['roles']
PARAMETERS, PROGRAMS, _ = catalog_configuration()
STANDARD = PARAMETERS['standard_temperature_c']


def seconds(key):
    """Convert a declared minute parameter; fixture schedules are not defaults."""
    return PARAMETERS[key] * 60


def named_program(identity):
    source = PROGRAMS[identity]
    return {'profile': identity, 'mode': 'progressive',
            'start_c': source['start_c'], 'end_c': source['end_c'],
            'gangs': source['distribution_gangs']}


def program_targets(identity):
    """Independent half-up interpolation of the configured catalogue points."""
    source = PROGRAMS[identity]
    count = source['distribution_gangs']
    return [math.floor(source['start_c'] +
                       (source['end_c']-source['start_c']) * step / max(1,count-1) + 0.5)
            for step in range(count)]
# A recorded physical short press:198.062 ms to release and302.757 ms
# from release to the native Shelly classification. These are fixture inputs,
# never an additional integration-side long-press threshold.
SHORT_RELEASE_S = INPUTS['button']['short_release_seconds']
NATIVE_CLASSIFY_AFTER_RELEASE_S = INPUTS['button']['native_classification_seconds']
COAST_TAIL = max(
    max(row[0] for row in SOURCE['curves']['coast']['rows']
        if row[index] == max(item[index] for item in SOURCE['curves']['coast']['rows']))
    for index in (1, 3)
)


def density(t, rh):
    """Water vapour density in g/m³ (Magnus saturation pressure)."""
    return 216.7 * (rh / 100) * 6.112 * math.exp(17.62 * t / (243.12 + t)) / (273.15 + t)


def relative(t, ah):
    return 100 * ah * (273.15 + t) / (216.7 * 6.112 * math.exp(17.62 * t / (243.12 + t)))


def physical(values):
    return [values[0], density(values[0], values[1]), values[2], density(values[2], values[3])]


def channels(values):
    return [round(values[0], INPUTS['numerics']['measurement_digits']), round(relative(values[0], values[1]), INPUTS['numerics']['measurement_digits']),
            round(values[2], INPUTS['numerics']['measurement_digits']), round(relative(values[2], values[3]), INPUTS['numerics']['measurement_digits'])]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def source_at(key, at):
    rows = SOURCE['curves'][key]['rows']
    lo = max((row for row in rows if row[0] <= at), key=lambda row: row[0])
    hi = next((row for row in rows if row[0] > at), lo)
    part = 0 if lo[0] == hi[0] else (at-lo[0])/(hi[0]-lo[0])
    return [a+(b-a)*part for a,b in zip(lo[1:],hi[1:])]


class Session:
    def __init__(self, identity, title, *, mode='automatic', program=None, presence_source='ha_presence'):
        self.id = identity
        self.rows = [[0, *SOURCE['curves']['cold_heat']['rows'][0][1:]]]
        self.t = 0.0
        self.blocks = []
        self.actions = [{'t':0,'name':'presence','args':{'value':False}}]
        self.human = []
        self.expected = []
        self.stays = []
        self.mode = mode
        self.presence_source = presence_source
        self.thermal_state = 'cold'
        self.heater_history = []
        self.cooling_history = []
        self.cooling_scope_start = 0.0
        self.pending_cooling = None
        self.inside = False
        self.ready_reached = False
        self.program = program or {'mode': 'constant', 'start_c': STANDARD, 'end_c': STANDARD, 'gangs': 1}
        self.current_target = self.program['start_c']
        self.document = {
            'schema_version': 1, 'id': identity, 'title': title,
            'kind': 'derived_whole_session',
            'configuration': {
                'parameter_source': 'configuration.json', 'control_mode': mode,
                'presence_source': presence_source, 'program': self.program,
                'button_input_kind': 'event', 'button_session_gesture': 'long',
                'button_program': 'constant', 'daylight': True},
            'measurement_clock': 'received_at',
            'actions_location': 'All controls are outside the cabin. No action lies within an uninterrupted stay.',
            'interpretation': 'Potential whole session assembled independently of detector and controller outputs.',
            'limitations': [
                'Temperature/moisture response is derived from measured shapes; this is not a claim that the combined session occurred.',
                'Human door movements label physical actions, not exact proxy-detection times.',
                'Declared thermal history selects the curve before execution. Product output never selects its own environment.',
            ],
        }

    @property
    def state(self):
        return self.rows[-1][1:]

    def action(self, name, args=None, *, t=None):
        if name=='button' and args and args.get('event')=='single_push':
            begin=self.t if t is None else t
            for delay,event in ((0,'btn_down'),(SHORT_RELEASE_S,'btn_up'),
                                (SHORT_RELEASE_S+NATIVE_CLASSIFY_AFTER_RELEASE_S,'single_push')):
                self.actions.append({'t':round(begin+delay,INPUTS['numerics']['time_digits']),'name':'button','args':{'event':event}})
            return
        self.actions.append({'t': round(self.t if t is None else t, INPUTS['numerics']['time_digits']), 'name': name, 'args': args or {}})
        if name == 'select_program' and args and 'start_c' in args:
            self.current_target = args['start_c']
            if self.state[0] < self.current_target:
                self.ready_reached = False

    def human_event(self, name, *, t=None, detail=None):
        e={'t': round(self.t if t is None else t, INPUTS['numerics']['time_digits']), 'name': name}
        if detail:e['detail']=detail
        self.human.append(e)

    def expect(self, identity, values, reason, *, at=None, between=None, eventually=False, presence_sources=None):
        e={'id': identity, 'values': values, 'reason': reason}
        if between:e['between_s']=[round(x,INPUTS['numerics']['time_digits']) for x in between]
        else:e['at_s']=round(self.t if at is None else at,INPUTS['numerics']['time_digits'])
        if eventually:e['eventually']=True
        if presence_sources:e['presence_sources']=presence_sources
        self.expected.append(e)

    def append(self, key, *, duration=None, offset=0, temperature_gain=None, lower_gain=None, thermal='heater_on', moisture=True, heater_off_after_s=None, heater_on_after_s=None):
        source=SOURCE['curves'][key]
        all_rows=source['rows']
        def value_at(at):
            lo=max((r for r in all_rows if r[0]<=at),key=lambda r:r[0])
            if lo[0]==at:return lo[1:]
            hi=next(r for r in all_rows if r[0]>at)
            f=(at-lo[0])/(hi[0]-lo[0])
            return [a+(b-a)*f for a,b in zip(lo[1:],hi[1:])]
        src=[[0,*value_at(offset)], *[[r[0]-offset,*r[1:]] for r in all_rows if r[0]>offset]]
        available=source['duration_s']-offset
        take=available if duration is None else min(duration,available)
        selected=[r for r in src if r[0]<=take]
        if selected[-1][0] < take:
            lo=selected[-1];hi=next(r for r in src if r[0]>take);f=(take-lo[0])/(hi[0]-lo[0])
            selected.append([take,*[a+(b-a)*f for a,b in zip(lo[1:],hi[1:])]])
        old=physical(self.state)
        origin=physical(src[0][1:])
        end=physical(selected[-1][1:])
        start=self.t
        for row in selected[1:]:
            val=physical(row[1:]);f=row[0]/take
            temps=[]
            for index, requested in ((0,temperature_gain),(2,lower_gain)):
                delta=val[index]-origin[index]
                if requested is not None:
                    full=end[index]-origin[index]
                    delta += f*(requested-full)
                temps.append(old[index]+delta)
            if key in {'coast','reheat_lag','entry','second_entry','exit','second_exit','late_absence_exit','mixed_absence_exit'}:
                # Door exchange/cooling relax the temperature gradient between
                # heights. Reusing an absolute lower-height drop indefinitely
                # would invent a sustained temperature inversion.
                initial_gap=max(old[0]-old[2],INPUTS['numerics']['minimum_height_gap_c'])
                source_gap=max(origin[0]-origin[2],INPUTS['numerics']['minimum_height_gap_c'])
                gap=initial_gap*max(val[0]-val[2],INPUTS['numerics']['minimum_height_gap_c'])/source_gap
                temps[1]=temps[0]-gap
            ah0=old[1]*(val[1]/origin[1]) if moisture else old[1]
            ah1=old[3]*(val[3]/origin[3]) if moisture else old[3]
            values=channels([temps[0],ah0,temps[1],ah1])
            timestamp=round(start+row[0],INPUTS['numerics']['time_digits'])
            if timestamp>self.rows[-1][0]:
                self.rows.append([timestamp,*values])
        self.t += take
        self.thermal_state = 'off' if ('heater_off' in thermal or thermal.endswith('then_off')) else 'on'
        if heater_off_after_s is not None:
            off_at = start + heater_off_after_s
            self.heater_history.append({'from_s': start, 'until_s': off_at, 'on': True, 'ready_idle': False})
            self.heater_history.append({'from_s': off_at, 'until_s': self.t, 'on': False, 'ready_idle': False})
        elif heater_on_after_s is not None:
            on_at = start + heater_on_after_s
            self.heater_history.append({'from_s': start, 'until_s': on_at, 'on': False, 'ready_idle': False})
            self.heater_history.append({'from_s': on_at, 'until_s': self.t, 'on': True, 'ready_idle': False})
            self.thermal_state='on'
        else:
            self.heater_history.append({'from_s': start, 'until_s': self.t,
                                        'on': self.thermal_state == 'on',
                                        'ready_idle': self.thermal_state == 'off' and self.ready_reached and not self.inside})
        if not self.inside and any(row[1] >= self.current_target for row in self.rows if row[0] >= start):
            self.ready_reached = True
        self.blocks.append({'at_s':round(start,INPUTS['numerics']['time_digits']),'until_s':round(self.t,INPUTS['numerics']['time_digits']),'curve_id':key,
                            'thermal_history':thermal,'heater_off_after_s':heater_off_after_s,'heater_on_after_s':heater_on_after_s,'transform':{'join':'upper temperature offset, measured inter-height gradient relaxation for door/cooling, vapour-density ratio at both heights','upper_gain_c':temperature_gain,'lower_gain_c':lower_gain,'moisture_from_source':moisture,'source_offset_s':offset}})

    def manual_infusion(self):
        """Measured infusion moisture over a separately bound thermostat cycle."""
        start = self.t
        first_row = len(self.rows)-1
        first_block = len(self.blocks)
        origin = physical(self.state)
        source = SOURCE['curves']['occupied_infusion']['rows']
        source_origin = physical(source[0][1:])
        self.heat(self.current_target+PARAMETERS['readiness_offset_c'])
        self.coast(SOURCE['curves']['occupied_infusion']['duration_s']-(self.t-start))
        for i in range(first_row+1,len(self.rows)):
            row = self.rows[i]
            t = row[0]-start
            lo = max((r for r in source if r[0] <= t),key=lambda r:r[0])
            hi = next((r for r in source if r[0] > t),lo)
            fraction = 0 if hi[0] == lo[0] else (t-lo[0])/(hi[0]-lo[0])
            climate = [a+(b-a)*fraction for a,b in zip(lo[1:],hi[1:])]
            moisture = physical(climate)
            row[2] = round(relative(row[1],origin[1]*moisture[1]/source_origin[1]),INPUTS['numerics']['measurement_digits'])
            row[4] = round(relative(row[3],origin[3]*moisture[3]/source_origin[3]),INPUTS['numerics']['measurement_digits'])
        for block in self.blocks[first_block:]:
            block['humidity_curve_id']='occupied_infusion'
            block['humidity_curve_at_s']=round(start,INPUTS['numerics']['time_digits'])
            block['transform']['moisture_from_source']=False
            block['transform']['moisture_join']='Measured infusion vapour-density ratio at both heights over the whole600-second infusion; temperatures follow the declared manual thermostat ON/OFF history.'

    def heat(self, target, *, minimum_duration=0):
        """Join the observed warming band once, never repeat its cold beginning."""
        start=self.t
        while self.state[0] < target-INPUTS['numerics']['temperature_join_tolerance_c']:
            key='cold_heat' if self.state[0] < INPUTS['thermal_join']['cold_hot_boundary_c'] else 'hot_heat'
            src=SOURCE['curves'][key]['rows']
            ref=min(max(self.state[0],src[0][1]),src[-1][1]-INPUTS['thermal_join']['source_end_offset_c'])
            offset=max((r[0] for r in src if r[1]<=ref),default=0)
            offset=min(offset,src[-1][0]-INPUTS['thermal_join']['minimum_source_tail_seconds'])
            subset=[r for r in src if r[0]>=offset]
            rate=(subset[-1][1]-subset[0][1])/(subset[-1][0]-subset[0][0])
            duration=min((target-self.state[0])/rate,subset[-1][0]-offset)
            gain=duration*rate
            ratio=(subset[-1][3]-subset[0][3])/(subset[-1][1]-subset[0][1])
            self.append(key,offset=offset,duration=duration,temperature_gain=gain,
                        lower_gain=gain*ratio,thermal='heater_on')
        if self.t-start < minimum_duration:
            self.dry(minimum_duration-(self.t-start),heater=True)

    def coast(self, duration):
        while duration>INPUTS['numerics']['duration_join_tolerance_s']:
            # Stored-heat bump is present only when the previous block heated.
            # Further off blocks use the monotonic tail of that same source.
            offset=0 if self.thermal_state=='on' else COAST_TAIL
            take=min(duration,SOURCE['curves']['coast']['duration_s']-offset)
            self.append('coast',offset=offset,duration=take,thermal='heater_off_after_heating' if offset==0 else 'heater_off_continuing')
            duration-=take

    def coast_to(self,target):
        """Stop a forward cooling segment at a specified physical temperature."""
        while self.state[0]>target+INPUTS['numerics']['temperature_join_tolerance_c']:
            offset=0 if self.thermal_state=='on' else COAST_TAIL
            source=SOURCE['curves']['coast']['rows']
            initial=min(source,key=lambda r:abs(r[0]-offset))[1]
            required=target-self.state[0]
            point=next((r for r in source if r[0]>offset and r[1]-initial<=required),source[-1])
            self.append('coast',offset=offset,duration=point[0]-offset,
                        thermal='heater_off_after_heating' if offset==0 else 'heater_off_continuing')

    def dry(self, duration, *, heater=True):
        if not heater:
            self.coast(duration)
            return
        while duration>INPUTS['numerics']['duration_join_tolerance_s']:
            take=min(duration,INPUTS['thermal_join']['dry_chunk_seconds'])
            plateau=SOURCE['curves']['internal_thermostat']
            ceiling=max(row[1] for row in plateau['rows'])
            heating=SOURCE['curves']['hot_heat']['rows']
            late_band=next(row for row in heating if row[0]==INPUTS['thermal_join']['late_heat_source_offset_seconds'])
            upper_rate=(heating[-1][1]-late_band[1])/(heating[-1][0]-late_band[0])
            if self.state[0]+take*upper_rate>ceiling:
                self.append('internal_thermostat',duration=take,thermal='heater_on_with_internal_thermostat',moisture=False)
            else:
                self.append('hot_heat',offset=INPUTS['thermal_join']['late_heat_source_offset_seconds'],duration=take,thermal='heater_on',moisture=False)
            duration-=take

    def wait_for_target(self,target):
        # Programme targets may decrease: cooling is physical forward time, not
        # a temperature jump or a reversed warm-up trace.
        self.current_target = target
        while self.state[0]>target+INPUTS['thermal_join']['target_coast_margin_c']:
            self.coast(min(INPUTS['thermal_join']['target_coast_max_seconds'],max(INPUTS['thermal_join']['target_coast_min_seconds'],(self.state[0]-target)*INPUTS['thermal_join']['target_coast_seconds_per_c'])))
        self.heat(target)

    def begin_cooling(self, start_s):
        """Bind the documented heat balance to a declared actuator history.

        This calculation is made before the product is run. The history comes
        from selected measured heating/off blocks, including the original
        actuator switch inside a measured exit. It is not read from a detector
        or a controller run. Exact detector scheduling remains subject to the
        broader recognition evaluations, rather than becoming a curve input.
        """
        half_life = PARAMETERS['oven_cooling_half_life_minutes'] * 60
        decay = math.log(2) / half_life
        heat = idle = 0.0
        for interval in self.heater_history:
            a = max(interval['from_s'], self.cooling_scope_start)
            b = min(interval['until_s'], start_s)
            if b <= a:
                continue
            weighted = (math.exp(-decay * (start_s - b)) - math.exp(-decay * (start_s - a))) / decay
            if interval['on']:
                heat += weighted
            elif interval['ready_idle']:
                idle += weighted
        base = PARAMETERS['after_run_minutes'] * 60
        maximum = PARAMETERS['oven_cooling_max_minutes'] * 60
        duration = base + max(0.0, min(maximum - base, heat / PARAMETERS['oven_cooling_heat_idle_ratio'] - idle))
        cycle = {
            'planned_start_s': round(start_s, INPUTS['numerics']['time_digits']),
            'planned_release_s': round(start_s + duration, INPUTS['numerics']['time_digits']),
            'weighted_heating_s': round(heat, INPUTS['numerics']['time_digits']),
            'weighted_ready_idle_s': round(idle, INPUTS['numerics']['time_digits']),
            'planned_duration_s': round(duration, INPUTS['numerics']['time_digits']),
            'history_from_s': round(self.cooling_scope_start, INPUTS['numerics']['time_digits']),
            'basis': 'Closed-form integral of the documented exponential weighting over the predeclared actuator intervals. Used to construct thermal continuation, not copied from product output.',
        }
        self.cooling_history.append(cycle)
        self.pending_cooling = cycle
        self.ready_reached = False
        return cycle

    def limit_proxy_cooling(self, person_cue_s):
        """Declare the new minimum rule before constructing its heat response.

        A source-cue time is a physical fixture construction point, not an oracle
        for detector scheduling. Recognition is evaluated separately in a window.
        """
        cycle = self.pending_cooling
        assert cycle is not None
        minimum = PARAMETERS['after_run_minutes']*60
        release = max(cycle['planned_start_s']+minimum, person_cue_s)
        cycle.setdefault('natural_release_s', cycle['planned_release_s'])
        cycle.setdefault('natural_duration_s', cycle['planned_duration_s'])
        cycle['planned_release_s'] = round(min(cycle['planned_release_s'], release), INPUTS['numerics']['time_digits'])
        cycle['planned_duration_s'] = round(cycle['planned_release_s']-cycle['planned_start_s'], INPUTS['numerics']['time_digits'])
        cycle['required_duration_s'] = minimum
        cycle['limiting_person_cue_s'] = round(person_cue_s, INPUTS['numerics']['time_digits'])
        cycle['release_reason'] = 'A proxy person indication limits the original cooling cycle to its configured minimum. Already elapsed OFF time counts; a later indication releases immediately.'
        return cycle

    def overlay_person_moisture(self, start, *, source_offset, duration):
        """A closed occupied segment consumes an unmodified measured AH shape."""
        initial_row = next(row for row in self.rows if abs(row[0]-start)<INPUTS['numerics']['duration_join_tolerance_s'])
        initial = physical(initial_row[1:])
        source_initial = physical(source_at('occupied_infusion', source_offset))
        end = start+duration
        for row in self.rows:
            if not start < row[0] <= end+INPUTS['numerics']['duration_join_tolerance_s']:
                continue
            source = physical(source_at('occupied_infusion', min(source_offset+row[0]-start,SOURCE['curves']['occupied_infusion']['rows'][-1][0])))
            row[2] = round(relative(row[1], initial[1]*source[1]/source_initial[1]),INPUTS['numerics']['measurement_digits'])
            row[4] = round(relative(row[3], initial[3]*source[3]/source_initial[3]),INPUTS['numerics']['measurement_digits'])
        for block in self.blocks:
            if block['until_s'] > start and block['at_s'] < end:
                block['humidity_overlay'] = {'curve_id':'occupied_infusion',
                    'at_s':round(start,INPUTS['numerics']['time_digits']), 'until_s':round(end,INPUTS['numerics']['time_digits']),
                    'source_offset_s':source_offset,
                    'method':'The real closed-occupancy and infusion vapour-density ratios at both heights replace the empty-cabin moisture recovery; temperature remains the separately declared OFF/ON response.'}

    def apply_reheat_overlay(self, release, end):
        """Retain measured wall recovery and add the measured delayed warming.

        The source ventilation continues to release stored wall heat after its
        contactor is OFF. After the declared new ON, only the rising branch of
        the independently recorded ON response is added. Both height channels
        retain their own delay and slope, while vapour density stays unchanged.
        This is explicitly a derived thermal continuation, not a replay claim.
        """
        source = SOURCE['curves']['reheat_lag']['rows']
        minima = {index: min(source, key=lambda row: row[index]) for index in (1, 3)}
        for row in self.rows:
            if not release < row[0] <= end:
                continue
            elapsed = row[0]-release
            measured = source_at('reheat_lag', min(elapsed, source[-1][0]))
            physical_row = physical(row[1:])
            for index in (1, 3):
                minimum = minima[index]
                if elapsed > minimum[0]:
                    physical_row[index-1] += max(0, measured[index-1]-minimum[index])
            row[1:] = channels(physical_row)
        updated = []
        for interval in self.heater_history:
            if interval['until_s'] <= release or interval['from_s'] >= end:
                updated.append(interval)
            else:
                if interval['from_s'] < release:
                    updated.append({**interval, 'until_s': release})
                updated.append({**interval, 'from_s': max(release, interval['from_s']),
                                'until_s': min(end, interval['until_s']), 'on': True, 'ready_idle': False})
                if interval['until_s'] > end:
                    updated.append({**interval, 'from_s': end})
        self.heater_history = updated
        for block in self.blocks:
            if block['until_s'] > release and block['at_s'] < end:
                block['thermal_restart_s'] = round(release, INPUTS['numerics']['time_digits'])
                block['reheat_overlay'] = {
                    'curve_id': 'reheat_lag', 'until_s': round(end, INPUTS['numerics']['time_digits']),
                    'method': 'Measured warming after each height reaches its post-ON minimum is added to the existing wall-recovery temperature. Original vapour density is retained.',
                    'derived': True,
                }
        self.thermal_state = 'on'

    def finish_cooling_history(self, number, *, target=None, completed=True):
        """Continue off only until the planned release, then use real inertia."""
        cycle = self.pending_cooling
        assert cycle is not None
        release = cycle['planned_release_s']
        if self.t < release:
            self.coast(release - self.t)
        if completed and release >= cycle.get('natural_release_s', release):
            self.cooling_scope_start = release
        self.pending_cooling = None
        if target is not None:
            self.current_target = target
        # A descending programme may still be above the thermostat threshold
        # when cooling ends. Ordinary thermostat off time has a separate cause.
        threshold = self.current_target - PARAMETERS['readiness_hysteresis_c']
        if self.state[0] > threshold:
            self.ready_reached = self.state[0] >= self.current_target
            self.coast_to(threshold)
            elapsed = 0.0
        else:
            elapsed = max(0.0, self.t-release)
            if elapsed:
                reheat = SOURCE['curves']['reheat_lag']['rows']
                if elapsed > reheat[-1][0]:
                    raise ValueError(f'{self.id}: exit overlaps the observed reheat inertia by too much ({elapsed:.1f}s)')
                # Heating does not instantly erase the relaxation after opening
                # a hot cabin. The source records90 seconds of further cooling
                # after confirmed EIN. A short overlapping end of the exit
                # therefore remains physically valid while power has returned.
                self.apply_reheat_overlay(release, self.t)
        resumed = self.t-elapsed
        self.append('reheat_lag',offset=elapsed,thermal='heater_on_with_observed_reheat_lag')
        cycle['resumed_heating_s'] = round(resumed, INPUTS['numerics']['time_digits'])
        self.expect(f'stay_{number}_cooling_over', {'cooling_active': False, 'heater_request': True},
                    'Cooling completes from the declared heat history; below the thermostat threshold the subsequent measured inertia belongs to a newly energised heater, not an extra heating prohibition.', at=self.t)

    def start(self, *, button=False):
        self.action('button',{'event':'single_push'}) if button else self.action('start')
        self.expect('started',{'recording':True,'operation_enabled':True,'control_mode':self.mode},
                    'One explicit start begins one recording; thermostat cycling must not split it.',at=1)

    def stay(self, number, *, infusion=True, exit_kind='second_exit', absence_delay=0, cooling=True, manual=False, short=False, brief_return=False, shared_door=False, next_target=None, exit_duration=None, entry_heating_delayed=False):
        start=self.t
        self.inside = True
        self.ready_reached = False
        self.human_event('enter',detail=f'Person enters for stay {number}; outside controls stop here.')
        self.action('presence',{'value':True},t=start+INPUTS['stay']['presence_entry_delay_seconds'])
        self.append('entry',thermal='heater_off_until_direct_entry_then_on' if entry_heating_delayed else ('heater_on' if not manual else 'manual_thermostat'),
                    heater_on_after_s=INPUTS['stay']['late_heating_seconds'] if entry_heating_delayed else None)
        if entry_heating_delayed:
            self.blocks[-1]['thermal_join_basis']='The first240 seconds are opening, room mixing and stored-wall recovery with the heater OFF. Only the completed directly confirmed entry returns heat; the source shape is joined continuously through this transition.'
        closure=self.t-INPUTS['stay']['closed_entry_tail_seconds']
        self.human_event('close_door',t=closure)
        self.expect(f'stay_{number}_started',{'active_gang':True,'cooling_active':False,'phase':'saunagang'},
                    'A full entry with continuing direct presence at sufficient temperature begins a gang.',
                    between=(self.t,self.t+90),eventually=True,presence_sources=['ha_presence'])
        if brief_return or shared_door:
            opened=self.t
            self.human_event('brief_exit_and_return' if brief_return else 'one_person_moves_other_remains')
            if brief_return:
                self.action('presence',{'value':False},t=opened+INPUTS['stay']['presence_entry_delay_seconds'])
                self.action('presence',{'value':True},t=opened+INPUTS['stay']['brief_return_presence_seconds'])
            self.append('entry',duration=INPUTS['stay']['brief_door_seconds'],temperature_gain=INPUTS['stay']['brief_temperature_gain_c'],lower_gain=INPUTS['stay']['brief_lower_gain_c'],thermal='heater_on')
            self.append('hot_heat',duration=INPUTS['stay']['brief_reheat_seconds'],temperature_gain=-INPUTS['stay']['brief_temperature_gain_c'],lower_gain=-INPUTS['stay']['brief_lower_gain_c'],thermal='heater_on',moisture=False)
            self.human_event('return_door_closed',t=opened+INPUTS['stay']['brief_close_seconds'])
            self.expect(f'stay_{number}_continues_after_brief_door',{'active_gang':True},
                        'A brief door movement without through-ventilation does not end the ongoing gang.',at=self.t,
                        presence_sources=['ha_presence'])
        if infusion:
            if manual:
                self.manual_infusion()
            else:
                self.append('occupied_infusion',temperature_gain=min(INPUTS['stay']['infusion_max_upper_gain_c'],max(r[1] for r in SOURCE['curves']['internal_thermostat']['rows'])-self.state[0]),
                            lower_gain=min(INPUTS['stay']['infusion_max_lower_gain_c'],max(r[3] for r in SOURCE['curves']['internal_thermostat']['rows'])-self.state[2]),
                            thermal='heater_on')
            self.human_event('infusion',t=self.t-INPUTS['stay']['infusion_before_end_seconds'],detail='Moisture sequence derived from the annotated closed-door infusion interval.')
            self.expect(f'stay_{number}_confirmed',{'active_gang':True,'phase':'saunagang'},
                        'The closed-door infusion stay is confirmed before the person exits.',at=self.t-30)
            if not manual:
                self.expect(f'stay_{number}_gang_heat',{'heater_request':True,'gang_heat_demand':True},
                            'An active confirmed gang keeps heating even above the normal thermostat threshold.',at=self.t-20)
        else:
            if manual:
                self.heat(self.current_target+PARAMETERS['readiness_offset_c'])
                self.coast_to(self.current_target-PARAMETERS['readiness_hysteresis_c'])
                self.append('reheat_lag',thermal='manual_thermostat_on_with_observed_reheat_lag')
                self.heat(self.current_target+PARAMETERS['readiness_offset_c'])
                self.coast(INPUTS['stay']['manual_after_heat_coast_seconds'])
            elif self.presence_source == 'proxy':
                # This measured entry closes120 s before its curve ends. The
                # remaining confirmation time consumes the configured limit.
                self.dry(max(0, seconds('confirmation_minutes')-INPUTS['stay']['closed_entry_tail_seconds']), heater=True)
                self.coast(INPUTS['stay']['unconfirmed_coast_seconds'])
                self.expect(f'stay_{number}_dry_unconfirmed',
                            {'active_gang':False,'gang_heat_demand':False,'cooling_active':False},
                            'The long dry stay has no infusion confirmation; its unchanged confirmation interval has elapsed without creating cooling.',
                            at=self.t, presence_sources=['proxy'])
            else:
                self.dry(INPUTS['stay']['dry_duration_seconds'] if not short else INPUTS['stay']['short_duration_seconds'],heater=True)
            self.expect(f'stay_{number}_dry_direct',{'active_gang':True},
                        'Direct presence does not require an infusion.',at=self.t-30,presence_sources=['ha_presence'])
        exit_curve_at=self.t
        if manual:
            exit_kind='late_absence_exit'
        final_infusion_seconds=0 if manual else INPUTS['stay']['final_infusion_seconds'].get(exit_kind,0)
        out=exit_curve_at+final_infusion_seconds
        if final_infusion_seconds:
            self.human_event('last_infusion_before_departure',detail='The live source begins with occupants still inside; its final moisture rise precedes departure.')
        self.human_event('leave_and_ventilate',t=out,detail='Everyone leaves; door remains open for through-ventilation.')
        absence=exit_curve_at+absence_delay if absence_delay else out+INPUTS['stay']['absence_delay_seconds']
        if exit_kind == 'late_absence_exit':
            planned_off = absence - exit_curve_at
        elif exit_kind == 'mixed_absence_exit':
            planned_off = INPUTS['stay']['mixed_exit_heater_off_seconds']
        else:
            planned_off = SOURCE['curves'][exit_kind].get('source_heater_switch_off_s', INPUTS['stay']['fallback_exit_heater_off_seconds'])
        dry_proxy = self.presence_source == 'proxy' and not infusion and not manual
        if manual:
            source = SOURCE['curves']['late_absence_exit']['rows']
            source_start = next(row for row in source if row[0]==INPUTS['stay']['manual_exit_offset_seconds'])
            difference = self.current_target-PARAMETERS['readiness_hysteresis_c']-self.state[0]
            switch_on = next((row[0]-INPUTS['stay']['manual_exit_offset_seconds'] for row in source if row[0]>=INPUTS['stay']['manual_exit_offset_seconds'] and row[1]-source_start[1] <= difference),0)
            self.append('late_absence_exit',offset=INPUTS['stay']['manual_exit_offset_seconds'],thermal='manual_thermostat_on_during_ventilation',heater_on_after_s=switch_on)
        elif dry_proxy:
            self.action('stop', t=out+INPUTS['stay']['outside_stop_seconds'])
            self.append(exit_kind,duration=exit_duration,thermal='heater_off_after_outside_stop')
        else:
            self.append(exit_kind,duration=exit_duration,thermal='heater_on_until_exit_evidence_then_off' if not manual else 'manual_thermostat',
                        heater_off_after_s=None if manual else planned_off)
        end=self.t
        # Absence can legitimately begin before the thermal proxy reacts or
        # after the physical door has closed. It never predates human exit.
        if not manual:
            self.thermal_state = 'on' if absence >= end-INPUTS['stay']['outside_stop_seconds'] else 'off'
        self.action('presence',{'value':False},t=absence)
        self.human_event('close_door_after_ventilation',t=end-INPUTS['stay']['ventilation_close_tail_seconds'])
        self.stays.append({'number':number,'entered_s':start,'left_s':out,'ventilation_until_s':end-INPUTS['stay']['ventilation_close_tail_seconds'],
                           'infusion':infusion,'presence_absent_s':absence,'exit_curve_at_s':exit_curve_at})
        self.inside = False
        if not manual and not dry_proxy:
            cycle = self.begin_cooling(exit_curve_at + planned_off)
            if self.presence_source == 'proxy':
                # In the unchanged source both heights show moisture recovery
                # after the shared minimum. By source420s this has continued
                # for160 seconds. The fixture binds the consequent heat return
                # in advance; it does not suppress this proxy person evidence.
                cycle = self.limit_proxy_cooling(exit_curve_at+INPUTS['stay']['proxy_recovery_cue_seconds'])
                cycle['cue_source'] = {'curve_id':exit_kind,'at_source_s':INPUTS['stay']['proxy_recovery_cue_seconds'],
                                      'basis':'Both source heights show sustained vapour-density recovery after ventilation; this person-like environmental signal is kept unchanged.'}
        self.expect(f'stay_{number}_ended',{'active_gang':False},
                    'The same exit opening, through-ventilation and continuing absence end the gang.',
                    between=(max(absence,end-30),end+10),eventually=True,presence_sources=['ha_presence'])
        if cooling and not manual:
            if dry_proxy:
                self.coast(PARAMETERS['session_gap_minutes']*60 - (end-out-INPUTS['stay']['outside_stop_seconds']) + INPUTS['stay']['after_run_margin_seconds'])
                self.expect('dry_proxy_shutdown_complete', {'recording':False,'operation_enabled':False,'cooling_active':False},
                            'The unconfirmed dry stay is not counted; an outside stop followed by the ordinary after-run closes the same session.',at=self.t,presence_sources=['proxy'])
            else:
                self.expect(f'stay_{number}_cooling',{'cooling_active':True,'heater_request':False,'phase':'nachlauf'},
                            'A confirmed ended gang is cooling before its independently calculated release.',
                            at=min(end,cycle['planned_start_s']+cycle['planned_duration_s']/2),
                            presence_sources=['ha_presence'] if not infusion else None)
                self.finish_cooling_history(number,target=next_target)
        return start,out,end

    def finish(self, *, button=False, afterrun=True):
        self.action('button',{'event':'single_push'}) if button else self.action('stop')
        stop=self.t
        self.expect('stopped',{'operation_enabled':False,'heater_request':False,'phase':'aus'},'Explicit operation off ends heating.',at=stop+2)
        self.coast(seconds('session_gap_minutes')+INPUTS['stay']['after_run_margin_seconds'] if afterrun else INPUTS['stay']['after_run_margin_seconds'])
        self.expect('finished',{'recording':False,'operation_enabled':False,'heater_request':False,'active_gang':False,'phase':'aus'},
                    'Whole session includes shutdown and expiry of the unmodified after-run interval.',at=self.t)

    def save(self):
        self.expect('no_unintended_fault',{'fault_count':0},
                    'Valid continuous measurements and ordinary human operation must not create device, sensor or operating faults.',
                    between=(1,self.t))
        action_times={a['t'] for a in self.actions}
        for check in self.expected:
            if check.get('at_s') in action_times and check['at_s']>0:
                check['at_s']=round(check['at_s']-0.2,INPUTS['numerics']['time_digits'])
                check['sampling']='before_the_next_control_action'
        self.document.update({'duration_s':round(self.t,INPUTS['numerics']['time_digits']),'measurement_file':self.id+'.measurements.json',
                              'measurement_sha256':'','curve_blocks':self.blocks,
                              'actions':sorted(self.actions,key=lambda x:x['t']),
                              'human_events':sorted(self.human,key=lambda x:x['t']),
                              'stays':self.stays,'expectations':self.expected,
                              'declared_heater_history':self.heater_history,
                              'cooling_continuations':self.cooling_history})
        rows={'schema_version':1,'roles':ROLES,'interpolation':'linear','channel_offsets_s':SOURCE['channel_offsets_s'],'rows':self.rows}
        path=HERE/(self.id+'.measurements.json')
        path.write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n')
        self.document['measurement_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        dump(HERE/(self.id+'.json'),self.document)
        return self.document


def base_scenario(identity, title, targets, *, mode='automatic', program=None, presence_source='ha_presence'):
    s=Session(identity,title,mode=mode,program=program,presence_source=presence_source)
    s.start(button=mode=='manual')
    if mode=='manual':
        s.action('set_light',{'value':INPUTS['base']['manual_light_percent']},t=INPUTS['base']['manual_light_at_seconds'])
    for i,target in enumerate(targets,1):
        s.wait_for_target(target)
        if identity.startswith('G04') and i==1:
            s.heat(target+PARAMETERS['readiness_offset_c']+INPUTS['base']['threshold_overshoot_c'])
            s.coast(INPUTS['base']['threshold_coast_seconds'])
            s.expect('automatic_upper_threshold_off',{'ready':True,'heater_request':False,'recording':True},
                     'The reached upper hysteresis threshold switches the thermostat off while readiness stays true.',at=s.t)
            s.coast_to(target-PARAMETERS['readiness_hysteresis_c']-INPUTS['base']['threshold_overshoot_c'])
            s.expect('automatic_lower_threshold_reheats',{'ready':True,'heater_request':True,'recording':True},
                     'The normal lower-threshold heating cycle preserves previously reached readiness after the configured heat pause.',at=s.t)
        s.expect(f'target_{i}',{'target_temperature_c':target},'The selected programme determines this stay target; no mode change occurs.',at=s.t)
        s.stay(i,infusion=i!=len(targets),manual=mode=='manual',cooling=mode!='manual',
               next_target=targets[min(i,len(targets)-1)])
        if identity == 'G04-proxy' and i == 1:
            first = s.stays[-1]
            exit_block = next(block for block in s.blocks
                              if block['at_s'] == round(first['exit_curve_at_s'],INPUTS['numerics']['time_digits']))
            s.expect('elapsed_minimum_person_signal_requests_heat_immediately',
                     {'cooling_active':False,'heater_request':True,'heater_command':True},
                     'Within the unchanged measured exit and moisture-recovery interval, a processed proxy person signal after the original minimum cooling time must release cooling and request and command heat in that input handler, before actuator acknowledgement or a later tick. At least one such handler must be reached.',
                     between=(first['exit_curve_at_s'],exit_block['until_s']))
            s.expected[-1]['input_transition'] = {
                'counter_increases':'proxy_person_signal_count',
                'before':{'cooling_active':True,
                          'cooling_elapsed_s':{'min':seconds('after_run_minutes')}},
            }
        if mode=='manual':
            if i==1:
                first=s.stays[-1]
                s.expect('manual_hysteresis_first_on',{'heater_request':True,'recording':True,'completed_sessions':0},
                         'Below the lower hysteresis threshold the manual thermostat heats within its current recording.',at=first['entered_s']-180)
                s.expect('manual_hysteresis_off',{'heater_request':False,'recording':True,'completed_sessions':0},
                         'After crossing the upper threshold the manual thermostat switches off without a minimum heat period.',at=first['left_s']-2)
                s.expect('manual_hysteresis_on_again',{'heater_request':True,'recording':True,'completed_sessions':0},
                         'Cooling through the lower threshold switches the manual thermostat back on without an automatic heating pause or a new recording.',at=first['ventilation_until_s'])
            s.expect(f'manual_{i}_no_additional_control',{'cooling_active':False,'gang_heat_demand':False,'temporary_door_heat':False,'light_percent':{'min':44,'max':46},'phase':'manuell'},
                        'Manual mode records and recognises, but only its thermostat controls the heater.',at=s.t)
    if mode=='manual':
        s.action('set_light',{'value':False})
        s.wait_for_target(STANDARD+PARAMETERS['readiness_offset_c'])
        s.coast(INPUTS['base']['manual_final_coast_seconds'])
        s.expect('manual_stop_in_heating_pause',{'heater_request':False,'operation_enabled':True},
                 'The thermostat may be off while manual operation remains enabled.',at=s.t)
    if mode=='manual' or presence_source != 'proxy':
        s.finish(button=mode=='manual')
    s.expect('completed_direct_session',{'gang_count':len(targets),'retained_sessions':1,'completed_sessions':1},
             'The full directly observed stays belong to one retained session; thermostat cycles do not split its recording.',at=s.t,presence_sources=['ha_presence'])
    s.expect('completed_proxy_session',{'gang_count':len(targets)-1,'retained_sessions':1,'completed_sessions':1},
             'Only the infusion-confirmed stays count under proxy detection; the last dry stay supplies no infusion confirmation.',at=s.t,presence_sources=['proxy'])
    return s.save()


def iteration_one():
    inputs = INPUTS['cases']['I01']
    initial = program_targets(inputs['initial_program'])
    lower = program_targets(inputs['lower_program'])
    s=Session('I01','Selections and next-gang override over five complete stays',
              program=named_program(inputs['initial_program']))
    s.start()
    s.heat(initial[0]+inputs['overshoot_c'])
    s.expect('initial_ready',{'ready':True,'target_temperature_c':initial[0]},'The initially selected lower target is reached.',at=s.t)
    s.human_event('draft_program_cancelled',detail='A higher individual draft is cancelled; no backend selection command is sent.')
    s.coast(inputs['draft_wait_seconds'])
    s.expect('cancel_preserves_program',{'ready':True,'target_temperature_c':initial[0],'program_mode':'progressive'},'Cancelling an uncommitted draft leaves the selected programme unchanged.',at=s.t)
    s.action('select_program',inputs['raised_program'])
    s.expect('raised_target_reheats',{'ready':False,'phase':'aufheizen','target_temperature_c':85},'An actual higher target above measured temperature needs heating again.',at=s.t+1)
    s.heat(inputs['raised_temperature_c'])
    s.expect('raised_target_ready',{'ready':True},'The newly selected higher target is now physically reached.',at=s.t)
    s.action('select_program',{'profile':inputs['lower_program']})
    s.coast(inputs['draft_wait_seconds'])
    s.expect('lower_target_keeps_ready',{'ready':True,'target_temperature_c':lower[0]},'A lower target already exceeded does not require reheating.',at=s.t)
    s.human_event('brief_door_without_entry')
    s.append('entry',temperature_gain=0.0,lower_gain=0.0,thermal='heater_off_stored_heat_at_brief_door')
    s.coast(inputs['empty_door_wait_seconds'])
    s.expect('empty_door_not_gang',{'active_gang':False,'gang_count':0},'A short empty door event five minutes before real entry must not create a gang.',at=s.t,presence_sources=['ha_presence'])
    s.stay(1,next_target=lower[min(1,len(lower)-1)])
    s.action('set_temperature',{'value':inputs['next_temperature_c']})
    s.expect('one_next_target',{'program_mode':'progressive','temperature_scope':'next_gang','next_target_temperature_c':82},'The dial changes the next gang only and retains the programme.',at=s.t+1)
    s.wait_for_target(inputs['next_temperature_c'])
    s.stay(2,next_target=lower[min(2,len(lower)-1)])
    next(check for check in s.expected if check['id']=='stay_2_confirmed')['values'].update(
        target_temperature_c=82, program_mode='progressive')
    s.expect('original_program_resumes',{'program_mode':'progressive','target_temperature_c':lower[min(2,len(lower)-1)]},'After the one altered gang, the original programme supplies its third target.',at=s.t)
    s.wait_for_target(lower[min(2,len(lower)-1)])
    s.stay(3,next_target=lower[-1])
    s.action('select_program',inputs['individual_program'])
    s.wait_for_target(inputs['individual_program']['start_c'])
    s.stay(4,next_target=inputs['individual_program']['steps'][1])
    s.action('select_program',{'mode':'constant'})
    s.expect('constant_uses_standard',{'program_mode':'constant','target_temperature_c':STANDARD,'temperature_scope':'current'},'Selecting constant uses the central standard, not a duplicate button or programme default.',at=s.t+1)
    s.action('set_temperature',{'value':inputs['constant_temperature_c']},t=s.t+2)
    s.wait_for_target(inputs['constant_temperature_c'])
    s.stay(5,brief_return=True,next_target=inputs['constant_temperature_c'])
    s.finish()
    s.expect('five_stays',{'gang_count':5,'retained_sessions':1,'completed_sessions':1},'Each complete stay is counted once; temporary exit and programme changes add no gang.',at=s.t,presence_sources=['ha_presence'])
    return s.save()


def iteration_two():
    inputs = INPUTS['cases']['I02']
    s=Session('I02','Independent override expiry, cooling decisions and entry during cooling',
              program=named_program(inputs['initial_program']))
    s.start()
    s.heat(inputs['first_override_temperature_c'])
    s.action('set_heater',{'value':False})
    s.action('set_light',{'value':inputs['first_light_percent']},t=s.t+1)
    s.coast(inputs['first_override_coast_seconds'])
    s.expect('explicit_choices',{'heater_override':False,'heater_request':False,'light_override':35},'Manual choices remain while the same heating phase and demand continue.',at=s.t)
    s.action('set_heater',{'value':None})
    s.action('set_light',{'value':None},t=s.t+1)
    s.heat(inputs['second_override_temperature_c'])
    s.action('set_heater',{'value':False})
    s.action('set_light',{'value':inputs['second_light_percent']},t=s.t+1)
    repeated=s.t+inputs['repeat_after_seconds']
    s.action('set_heater',{'value':False},t=repeated)
    s.action('set_light',{'value':inputs['second_light_percent']},t=repeated+1)
    s.coast(seconds('manual_override_minutes')-1)
    s.expect('override_not_expired_early',{'heater_override':False,'heater_request':False},'No phase or demand change occurred and the configured override duration is not yet over.',at=s.t)
    s.coast(inputs['expiry_late_margin_seconds'])
    s.expect('repeat_does_not_extend',{'heater_override':None,'light_override':None},'Repeated reached choices cannot extend the original configured deadline.',at=s.t)
    s.heat(inputs['third_override_temperature_c'])
    s.action('set_heater',{'value':False})
    s.action('set_light',{'value':inputs['third_light_percent']},t=s.t+1)
    s.coast(inputs['entry_coast_seconds'])
    s.stay(1,exit_kind='late_absence_exit',absence_delay=inputs['late_absence_seconds'],cooling=False,entry_heating_delayed=True)
    end=s.t
    s.expect('phase_returns_heater',{'heater_override':None},'Entry into a confirmed gang returns the temporary heater choice even though automatic heat remains requested.',at=s.stays[-1]['entered_s']+450,presence_sources=['ha_presence'])
    s.expect('late_absence_ends',{'active_gang':False,'cooling_active':True,'heater_request':False},'Absence after door closure completes the already observed exit; the old gang cannot continue heating.',at=end+1,presence_sources=['ha_presence'])
    s.action('button',{'event':'single_push'},t=end+inputs['cooling_button_after_seconds'])
    s.coast(inputs['cooling_button_coast_seconds'])
    s.expect('cooling_rejects_manual_on',{'heater_request':False,'cooling_active':True,'fault_count':0},'Short-press heater admission during cooling is rejected without creating a session-start fault.',at=s.t)
    s.finish_cooling_history(1,target=program_targets('hoehenwanderung')[1])
    s.expect('first_cooling_elapsed',{'cooling_active':False},'The complete configured cooling duration elapsed.',at=s.t)
    s.action('select_program',inputs['upper_program'])
    s.wait_for_target(inputs['upper_temperature_c'])
    s.coast(inputs['upper_coast_seconds'])
    s.action('set_heater',{'value':True})
    s.append('internal_thermostat',offset=inputs['thermostat_source_offset_seconds'],duration=inputs['thermostat_duration_seconds'],thermal='heater_on_with_internal_thermostat')
    s.expect('manual_on_from_heat_pause',{'heater_override':True,'heater_request':True},'An explicit on choice during ordinary thermostat off starts manual override.',at=s.t)
    s.action('set_heater',{'value':None})
    s.coast_to(inputs['reheat_lower_temperature_c'])
    s.heat(inputs['reheat_upper_temperature_c'])
    s.append('internal_thermostat',thermal='heater_on_with_internal_thermostat')
    s.action('set_heater',{'value':False})
    s.coast(inputs['demand_change_coast_seconds'])
    s.action('select_program',{'profile':'genusszeit'})
    s.expect('different_demand_returns_override',{'heater_override':None,'target_temperature_c':PROGRAMS['genusszeit']['start_c']},'A substantially lower selected target changes the automatic demand from on to off and releases the override.',at=s.t+1)
    s.wait_for_target(inputs['return_temperature_c'])
    s.stay(2,exit_kind='mixed_absence_exit',absence_delay=inputs['mixed_absence_seconds'],cooling=False)
    for transition in inputs['mixed_presence']:
        s.action('presence',{'value':transition['value']},t=s.stays[-1]['exit_curve_at_s']+transition['after_seconds'])
    requested_end=s.stays[-1]['exit_curve_at_s']+inputs['finish_cooling_after_seconds']
    s.action('finish_cooling',t=requested_end)
    s.pending_cooling['natural_release_s']=s.pending_cooling['planned_release_s']
    s.pending_cooling['planned_release_s']=round(requested_end,INPUTS['numerics']['time_digits'])
    s.pending_cooling['release_reason']='Explicit outside action after the final continuing absence and before natural completion.'
    s.finish_cooling_history('second_manually_ended',target=program_targets('genusszeit')[1],completed=False)
    s.expect('explicit_cooling_end',{'cooling_active':False},'Explicitly ending cooling removes only cooling; the next gang remains possible.',at=requested_end+1)
    s.wait_for_target(program_targets('genusszeit')[1])
    s.stay(3,cooling=False,exit_duration=inputs['short_exit_seconds'])
    s.coast(inputs['before_reentry_coast_seconds'])
    s.stay(4,cooling=False,entry_heating_delayed=True)
    s.expect('fourth_entry_cancels_cooling',{'active_gang':True,'cooling_active':False},'A genuine new entry during cooling begins a gang and ends cooling.',at=s.stays[-1]['entered_s']+450,presence_sources=['ha_presence'])
    s.finish()
    s.expect('four_stays_one_session',{'gang_count':4,'retained_sessions':1,'completed_sessions':1},
             'Overrides, delayed absence and cooling choices do not split or multiply the four complete stays or their containing session.',at=s.t,presence_sources=['ha_presence'])
    return s.save()


def session_gesture(s):
    begin=s.t
    s.actions.append({'t':round(begin,INPUTS['numerics']['time_digits']),'name':'button','args':{'event':'btn_down'},'gestures':['long']})
    s.actions.append({'t':round(begin+INPUTS['button']['long_classification_seconds'],INPUTS['numerics']['time_digits']),'name':'button','args':{'event':'long_push'},'gestures':['long']})
    s.actions.append({'t':round(begin+INPUTS['button']['long_release_seconds'],INPUTS['numerics']['time_digits']),'name':'button','args':{'event':'btn_up'},'gestures':['long']})
    for gesture,count in (('double',2),('triple',3)):
        for click in range(count):
            down=begin+click*SHORT_RELEASE_S*2
            s.actions.append({'t':round(down,INPUTS['numerics']['time_digits']),'name':'button','args':{'event':'btn_down'},'gestures':[gesture]})
            s.actions.append({'t':round(down+SHORT_RELEASE_S,INPUTS['numerics']['time_digits']),'name':'button','args':{'event':'btn_up'},'gestures':[gesture]})
        classified=begin+(count*2-1)*SHORT_RELEASE_S+NATIVE_CLASSIFY_AFTER_RELEASE_S
        s.actions.append({'t':round(classified,INPUTS['numerics']['time_digits']),'name':'button','args':{'event':gesture+'_push'},'gestures':[gesture]})
    return begin


def iteration_three():
    inputs = INPUTS['cases']['I03']
    s=Session('I03','Outside buttons, early entry, multiple people, automatic continuation')
    s.action('button',{'event':'single_push'})
    s.expect('short_starts_manual',{'control_mode':'manual','operation_enabled':True,'target_temperature_c':STANDARD,'recording':True,'completed_sessions':0,'retained_sessions':0},'A short physical press while automatic operation is off starts manual control at the central standard.',at=1)
    s.heat(inputs['manual_preheat_temperature_c'])
    original_preheat_end=s.t
    s.action('button',{'event':'single_push'})
    s.dry(SHORT_RELEASE_S+NATIVE_CLASSIFY_AFTER_RELEASE_S,heater=True)
    s.coast(inputs['manual_pause_seconds'])
    s.expect('manual_pause_keeps_recording',{'control_mode':'manual','operation_enabled':False,'heater_request':False,'recording':True,'completed_sessions':0,'session_started_s':{'since_s':1,'min':0,'max':0}},
             'Manual OFF stops heating but keeps the same recording throughout this five-minute interruption, which is shorter than the normal after-run.',at=s.t)
    restarted=s.t
    s.action('button',{'event':'single_push'})
    s.coast(SHORT_RELEASE_S+NATIVE_CLASSIFY_AFTER_RELEASE_S)
    s.append('reheat_lag',thermal='manual_thermostat_on_with_observed_reheat_lag')
    s.expect('manual_restart_keeps_recording',{'control_mode':'manual','operation_enabled':True,'recording':True,'completed_sessions':0,'session_started_s':{'since_s':1,'min':0,'max':0}},
             'Manual ON within after-run resumes the original recording without creating a second fragment.',at=restarted+2)
    s.heat(inputs['manual_preheat_temperature_c'])
    s.action('set_light',{'value':'$initial_light'})
    s.dry(inputs['before_gesture_seconds'])
    begin=session_gesture(s)
    s.heat(PARAMETERS['sauna_min_temperature_c']-inputs['early_entry_below_minimum_c'])
    s.expect('button_selection_wins',{'control_mode':'automatic','target_temperature_c':'$button_target','program_mode':'$button_mode','selected_program_id':'$button_program_id','completed_sessions':0,'retained_sessions':0,'gang_count':0,'recording':True,'session_started_s':{'since_s':1,'min':0,'max':0}},'The configured button programme wins over the panel selection. Switching from manual preheating to automatic operation continues the same recording with its original start and retains its existing preheating measurements.',at=begin+2)
    entered=s.t
    s.human_event('enter_below_minimum',detail='A enters and remains while the cabin warms through the configured minimum.')
    s.action('presence',{'value':True},t=entered+inputs['presence_delay_seconds'])
    s.append('entry',thermal='heater_on')
    s.expect('cold_entry_waits',{'active_gang':False},'A cold entry alone must not evade the unchanged minimum temperature.',at=s.t)
    crossed_begin=s.t
    s.heat(PARAMETERS['sauna_min_temperature_c']+inputs['minimum_crossing_above_c'])
    s.expect('minimum_crossing_starts_gang',{'active_gang':True,'cooling_active':False},'Continuing presence and the existing entry become a gang when the real minimum is reached; no new door action is needed.',between=(crossed_begin,s.t),eventually=True,presence_sources=['ha_presence'])
    joined=s.t
    s.human_event('second_person_joins')
    s.append('entry',temperature_gain=inputs['joined_upper_gain_c'],lower_gain=inputs['joined_lower_gain_c'],thermal='heater_on')
    s.human_event('first_person_briefly_leaves_and_returns',t=joined+inputs['brief_leave_after_seconds'],detail='The second person remains; direct presence stays on throughout.')
    s.append('occupied_infusion',thermal='heater_on')
    out=s.t
    s.human_event('both_leave_and_ventilate')
    s.action('presence',{'value':False},t=out+inputs['absence_after_seconds'])
    s.append('second_exit',thermal='heater_on_until_exit_then_off',heater_off_after_s=SOURCE['curves']['second_exit']['source_heater_switch_off_s'])
    s.stays.append({'number':1,'entered_s':entered,'left_s':out,'ventilation_until_s':s.t-inputs['ventilation_tail_seconds'],'infusion':True,'presence_absent_s':out+inputs['absence_after_seconds']})
    s.expect('one_joint_stay',{'gang_count':1,'active_gang':False},'Joining and one person moving while another remains must not split the shared stay.',at=s.t,presence_sources=['ha_presence'])
    s.inside=False
    s.begin_cooling(out+SOURCE['curves']['second_exit']['source_heater_switch_off_s'])
    s.finish_cooling_history(1,target=min(program_targets('genusszeit')[1],STANDARD))
    s.heat(inputs['override_temperature_c'])
    s.action('button',{'event':'single_push'})
    s.action('set_light',{'value':False},t=s.t+inputs['light_off_after_seconds'])
    s.action('set_light',{'value':True},t=s.t+inputs['light_on_after_seconds'])
    s.action('set_light',{'value':inputs['light_percent']},t=s.t+inputs['light_value_after_seconds'])
    s.coast(inputs['override_coast_seconds'])
    s.expect('short_auto_override',{'heater_override':False,'target_temperature_c':'$second_button_target','program_mode':'$button_mode','selected_program_id':'$button_program_id'},'The short press in an automatic session changes only heater override.',at=s.t)
    s.action('button',{'event':'single_push'})
    s.action('set_light',{'value':None},t=s.t+1)
    s.heat(inputs['return_temperature_c'])
    s.expect('second_short_returns',{'heater_override':None,'control_mode':'automatic','target_temperature_c':'$second_button_target','program_mode':'$button_mode','selected_program_id':'$button_program_id'},'The next short press returns the existing override to automatic operation.',at=s.t)
    s.stay(2,shared_door=True,cooling=False)
    s.action('set_light',{'value':'$final_light'})
    s.coast(inputs['final_light_coast_seconds'])
    session_gesture(s)
    s.coast(seconds('session_gap_minutes')+inputs['after_run_margin_seconds'])
    s.expect('gesture_ended',{'operation_enabled':False,'recording':False,'active_gang':False,'retained_sessions':1,'completed_sessions':1,'gang_count':2,'session_started_s':{'since_s':1,'min':0,'max':0},'archived_preheat_roles':4,'recording_history_intact':True},'The configured gesture ends the one continuous session and its full light after-run. The original manual preheating, its short OFF/ON interruption, and both automatic stays are retained together. SQLite must retain all four original measurement roles, the heating and contactor history, and accumulated energy under the same session ID.',at=s.t)
    s.document['preserve_measurements_before_s']=round(original_preheat_end,INPUTS['numerics']['time_digits'])
    doc=s.save()
    return doc


def iteration_four():
    inputs = INPUTS['cases']['I04']
    s=Session('I04','Early stay, resume during after-run and final shutdown before ventilation')
    s.start()
    s.heat(PARAMETERS['sauna_min_temperature_c']+inputs['entry_above_minimum_c'])
    s.stay(1,infusion=False,short=True,cooling=False)
    s.expect('exit_below_minimum',{'active_gang':False,'gang_count':1},'An existing gang must end while ventilation lowers temperature beneath the minimum for new starts.',at=s.t,presence_sources=['ha_presence'])
    s.action('stop')
    s.coast(inputs['restart_coast_seconds'])
    s.expect('afterrun_recording',{'operation_enabled':False,'recording':True,'light_after_run':True},'A normal stop keeps its recording available during the unmodified restart interval.',at=s.t)
    s.action('start')
    s.expect('resume_same_recording',{'operation_enabled':True,'recording':True,'completed_sessions':0},'Restart during after-run resumes the same session rather than retaining another one.',at=s.t+1)
    s.wait_for_target(inputs['second_target_c'])
    # All controls remain outside. A person staying inside prevents a short
    # intermediate door movement from terminating the second stay.
    entered=s.t
    s.human_event('enter')
    s.action('presence',{'value':True},t=entered+inputs['presence_delay_seconds'])
    s.append('entry',thermal='heater_on')
    s.human_event('brief_door_other_person_remains')
    s.append('entry',duration=inputs['brief_door_seconds'],temperature_gain=inputs['brief_upper_gain_c'],lower_gain=inputs['brief_lower_gain_c'],thermal='heater_on')
    s.append('hot_heat',duration=inputs['reheat_seconds'],temperature_gain=-inputs['brief_upper_gain_c'],lower_gain=-inputs['brief_lower_gain_c'],thermal='heater_on',moisture=False)
    s.append('occupied_infusion',thermal='heater_on')
    out=s.t
    s.human_event('leave_before_outside_stop')
    s.action('stop',t=out+inputs['outside_stop_seconds'])
    s.action('presence',{'value':False},t=out+inputs['absence_after_seconds'])
    s.append('second_exit',thermal='heater_off_after_explicit_stop')
    s.stays.append({'number':2,'entered_s':entered,'left_s':out,'ventilation_until_s':s.t-inputs['ventilation_tail_seconds'],'infusion':True,'presence_absent_s':out+inputs['absence_after_seconds']})
    s.expect('stop_ends_existing_gang',{'active_gang':False,'operation_enabled':False},'Explicit off after leaving ends the open gang without waiting for delayed proxy evidence.',at=out+6)
    s.action('set_light',{'value':inputs['after_run_light_percent']})
    s.coast(inputs['after_run_light_coast_seconds'])
    s.action('set_light',{'value':None})
    s.coast(max(0,out+inputs['outside_stop_seconds']+seconds('session_gap_minutes')+inputs['after_run_margin_seconds']-s.t))
    s.expect('final_end_not_extended',{'recording':False,'operation_enabled':False,'light_after_run':False,'retained_sessions':1,'completed_sessions':1,'gang_count':2,'recording_history_intact':True},'Manual light action in after-run does not extend its original session deadline. Resumption preserves accumulated energy and the original heating and contactor history through the stored completion.',at=s.t)
    return s.save()


def iteration_five():
    inputs = INPUTS['cases']['I05']
    s=Session('I05-proxy','Proxy return through the still-open door: minimum cooling with repeated person evidence',
              presence_source='proxy')
    s.start()
    # A usable entry while the standard selection is still warming. This is an
    # ordinary human choice, not a lower thermostat setting for the test.
    s.heat(STANDARD-inputs['early_entry_below_target_c'])
    s.stay(1,cooling=False,exit_duration=inputs['short_exit_seconds'])
    out=s.stays[-1]['exit_curve_at_s']
    returned=out+inputs['return_after_seconds']
    closed=out+inputs['close_after_seconds']
    # The person returns during the still-open ventilation, then closes it.
    # The source's falling branch ends at150s in this derived shorter opening;
    # its measured closed-room recovery begins continuously at its220s minimum.
    s.stays[-1]['ventilation_until_s']=round(closed,INPUTS['numerics']['time_digits'])
    next(event for event in reversed(s.human) if event['name']=='close_door_after_ventilation')['t']=round(closed,INPUTS['numerics']['time_digits'])
    s.human_event('return_through_still_open_door',t=returned,
                  detail='The first stay has ended. The person returns through the same still-open ventilation door; this is not a newly observed opening event.')
    s.human_event('close_door_with_returned_person',t=closed)
    s.inside=True
    s.expect('first_exit_is_cooling',{'active_gang':False,'cooling_active':True,'heater_request':False},
             'The first confirmed stay has ended and cooling is already active before the returned person closes the door.',at=returned)
    cycle=s.limit_proxy_cooling(out+inputs['person_cue_after_seconds'])
    cycle['cue_source']={'curve_id':'occupied_infusion','source_offset_s':inputs['moisture_source_offset_seconds'],
                        'closed_occupancy_at_s':round(closed,INPUTS['numerics']['time_digits']),
                        'basis':'The measured closed-door person/infusion moisture follows physical return and closure; source120–600 is reused without changing its moisture ratios.'}
    s.append('second_exit',offset=inputs['recovery_source_offset_seconds'],thermal='heater_off_wall_recovery_after_shorter_ventilation')
    s.blocks[-1]['physical_join']='The same person returns while the door is still open. Closing at150s ends the shortened ventilation fall; measured wall recovery from the shared source minimum at220s follows with continuous temperatures and vapour density.'
    s.finish_cooling_history('proxy_return',target=STANDARD)
    moisture_end=closed+inputs['moisture_duration_seconds']
    s.dry(moisture_end-s.t,heater=True)
    s.overlay_person_moisture(closed,source_offset=inputs['moisture_source_offset_seconds'],duration=inputs['moisture_duration_seconds'])
    s.human_event('infusion_after_return',t=closed+inputs['infusion_after_seconds'],
                  detail='The source moisture rise at210s lies90s after the reused120s start; the person is inside behind the closed door.')
    s.expect('person_signal_limits_to_minimum',
             {'cooling_active':True,'heater_request':False,'cooling_duration_s':seconds('after_run_minutes'),
              'cooling_minimum_s':seconds('after_run_minutes'),'proxy_person_signal_count':{'since_s':returned,'min':1}},
             'Any real processed proxy person indication limits total cooling to the configured minimum; it cannot instantly cancel the still-unelapsed part.',
             between=(out+280,out+350))
    s.expect('repeated_signals_keep_original_clock',
             {'cooling_active':True,'heater_request':False,'cooling_duration_s':seconds('after_run_minutes'),
              'cooling_remaining_s':{'min':0,'max':seconds('after_run_minutes')-(out+360-returned)},
              'proxy_person_signal_count':{'since_s':returned,'min':2}},
             'Cooling was already active at the declared return. The remaining time is bounded by the configured total minimum minus that observed elapsed interval. The second separate source moisture rise must not restart or extend the clock.',between=(out+360,out+390))
    s.expect('original_minimum_expires',{'cooling_active':False,'heater_request':True},
             'The original minimum has elapsed. The below-target cabin may heat although the returning person did not provide a separate new opening event.',at=out+450)
    s.dry(inputs['remaining_stay_seconds'],heater=True)
    left=s.t
    s.human_event('returning_person_leaves_before_outside_stop')
    s.inside=False
    s.action('stop',t=left+inputs['outside_stop_seconds'])
    s.append('second_exit',thermal='heater_on_until_outside_stop_then_off',heater_off_after_s=inputs['outside_stop_seconds'])
    s.human_event('close_door_after_final_ventilation',t=left+inputs['final_close_seconds'])
    s.stays.append({'number':2,'entered_s':round(returned,INPUTS['numerics']['time_digits']),'left_s':round(left,INPUTS['numerics']['time_digits']),
                    'ventilation_until_s':round(left+inputs['final_close_seconds'],INPUTS['numerics']['time_digits']),'infusion':True,
                    'proxy_entry_ambiguity':'Return took place during the previous open ventilation event; cooling control is deliberately checked independently of permission to create another gang.'})
    s.expect('outside_stop_ends_operation',{'operation_enabled':False,'heater_request':False,'active_gang':False},
             'The person stops operation outside after leaving, independently of the proxy classification of the return.',at=left+6)
    s.coast(PARAMETERS['session_gap_minutes']*60-(s.t-left-inputs['outside_stop_seconds'])+inputs['after_run_margin_seconds'])
    s.expect('one_retained_session',{'operation_enabled':False,'recording':False,'retained_sessions':1,'completed_sessions':1},
             'The initial confirmed gang retains exactly one session. Cooling signals and a return through an already-open door do not create extra recordings.',at=s.t)
    s.document['limitations'].append('The returned human stay has no independent new opening signal. Its gang-count classification is not invented by the fixture; the person-signal cooling contract is evaluated independently of that eligibility.')
    return s.save()


def main():
    dump(HERE/'configuration.json', configuration_reference())
    docs=[]
    saved_targets=program_targets(INPUTS['base']['saved_program'])
    for presence_source in ('ha_presence','proxy'):
        suffix='' if presence_source=='ha_presence' else '-proxy'
        docs.append(base_scenario('G01'+suffix,'Gespeichertes Programm: all stages, then another stay',saved_targets+[saved_targets[-1]],
                                 program=named_program(INPUTS['base']['saved_program']),presence_source=presence_source))
        docs.append(base_scenario('G02'+suffix,'Individuell gleichmäßig: descending stages without reselection',INPUTS['cases']['G02']['targets'],
                                 program=INPUTS['cases']['G02']['program'],presence_source=presence_source))
        docs.append(base_scenario('G03'+suffix,'Einzelne Stufen: ascending, equal, descending and held final targets',INPUTS['cases']['G03']['targets'],
                                 program=INPUTS['cases']['G03']['program'],presence_source=presence_source))
        docs.append(base_scenario('G04'+suffix,'Konstant: complete session without changing the central standard target',[STANDARD]*INPUTS['base']['standard_stays'],presence_source=presence_source))
    docs.append(base_scenario('G05','Manuell: button start, thermostat operation, button stop in heat pause',[STANDARD]*INPUTS['base']['standard_stays'],mode='manual'))
    docs.append(iteration_one())
    docs.append(iteration_two())
    docs.append(iteration_three())
    docs.append(iteration_four())
    docs.append(iteration_five())
    for mode in ('automatic','manual'):
        s=Session('G00-'+mode,'Complete aborted session without a human stay',mode=mode)
        s.start(button=mode=='manual')
        s.heat(INPUTS['cases']['G00']['abort_temperature_c'])
        s.expect('unused',{'active_gang':False,'gang_count':0},'Heating an empty sauna is not a human stay.',at=s.t)
        s.finish(button=mode=='manual')
        s.expect('not_retained',{'retained_sessions':0},'An unused session has no confirmed gang and is not retained.',at=s.t)
        docs.append(s.save())
    manifest={'schema_version':2,'source_curves':'fixtures/scenarios/source_curves.json',
              'source_curves_sha256':hashlib.sha256((HERE/'source_curves.json').read_bytes()).hexdigest(),
              'construction_file':'fixtures/scenarios/construction.json',
              'construction_sha256':hashlib.sha256((HERE/'construction.json').read_bytes()).hexdigest(),
              'configuration_file':'fixtures/scenarios/configuration.json',
              'configuration_sha256':hashlib.sha256((HERE/'configuration.json').read_bytes()).hexdigest(),
              'configuration_policy':'The canonical defaults catalog is consumed once through its bound source reference; effective parameter changes require explicit input review; presentation-only changes do not rematerialize curves. Explicit user programme selections and historical replay configurations are independent data.',
              'scenarios':[{'id':d['id'],'path':'fixtures/scenarios/'+d['id']+'.json','sha256':hashlib.sha256((HERE/(d['id']+'.json')).read_bytes()).hexdigest(),'presence_sources':['ha_presence','proxy']} for d in docs]}
    for item in manifest['scenarios']:
        if item['id'].startswith(('G01','G02','G03','G04')):
            item['presence_sources']=['proxy' if item['id'].endswith('-proxy') else 'ha_presence']
        if item['id']=='I03':
            item['presence_sources']=['ha_presence']
            item['variants']=[]
            for gesture,event in [('long','long_push'),('double','double_push'),('triple','triple_push')]:
                for choice,initial,final in [('genusszeit',PARAMETERS['operation_brightness_percent'],0),('constant',0,PARAMETERS['operation_brightness_percent'])]:
                    item['variants'].append({'id':gesture+'-'+choice,
                      'configuration':{'button_session_gesture':gesture,'button_program':choice},
                      'action_substitutions':{'session_gesture':event,'initial_light':initial,'final_light':final},
                      'expected_substitutions':{'button_target':saved_targets[0] if choice=='genusszeit' else STANDARD,'second_button_target':saved_targets[min(1,len(saved_targets)-1)] if choice=='genusszeit' else STANDARD,'button_mode':'progressive' if choice=='genusszeit' else 'constant','button_program_id':'genusszeit' if choice=='genusszeit' else None}})
        elif item['id']=='I05-proxy':
            item['presence_sources']=['proxy']
        elif item['id'].startswith('I'):
            item['presence_sources']=['ha_presence']
    dump(HERE.parent.parent/'scenario_manifest.json',manifest)
    provenance={
      'schema_version':1,
      'purpose':'Independent potential whole sessions, not replay labels copied from historical detections.',
      'required_files':'All inputs, configuration, actions and evaluations are committed with SHA-256 bindings. No private path or environment variable is needed for execution.',
      'source_count':len(SOURCE['curves']),
      'source_curves_sha256':hashlib.sha256((HERE/'source_curves.json').read_bytes()).hexdigest(),
      'derivation':[
        'Four climate channels are transferred together in temperature and water-vapour density; relative humidity is recalculated with the documented Magnus saturation-pressure relationship.',
        'The observed temperature difference between heights relaxes proportionally during door exchange and cooling. Repeating absolute lower-height differences must not invent a thermal inversion.',
        'Warm-up uses the measured temperature band, not repeated cold-start snippets. A stored-heat bump occurs only immediately after a heating interval; continuing off time consumes the monotonic cooling tail.',
        'The cooling release is calculated before execution from the documented exponential heat balance and the declared measured actuator history. The curve resumes heating at that release, including the observed initial90 seconds of continued cooling and225-second recovery segment.',
        'Direct and proxy cases reuse the same measured infusion shapes. Their thermal continuation differs when proxy person evidence limits cooling or a dry proxy stay remains unconfirmed.',
        'Proxy cooling shortened to the minimum retains the original heat-balance scope. A measured delayed ON response is superimposed on remaining wall-heat recovery without changing vapour density.',
        'Manual infusion temperatures follow independently selected thermostat heating/off blocks. The measured two-height infusion vapour-density sequence is applied over that temperature path; manual exit uses the measured continuously heated ventilation source.',
        'Programme changes and overrides select the physical heat/off history before product execution. A wrong product output cannot steer the input fixture.',
        'Stützwerte are linearly interpolated, with representative asynchronous receipt offsets. Exact raw timing belongs to the separate real-session replay suite.',
      ],
      'assessment_basis':[
        'User-described external controls and physical entry, stay, infusion, departure and through-ventilation.',
        'The configured minimum gates only new gang starts; existing gang ends remain evaluable below it.',
        'Direct presence confirms a complete entry; its delayed absence can complete the same ventilated exit after closure.',
        'A dry stay is confirmed with direct presence but has no infusion confirmation under proxy detection.',
        'Any processed proxy person indication limits a running cooling cycle to its original configured minimum; elapsed OFF time counts and repeated indications cannot restart its clock. Directly confirmed entry ends cooling immediately.',
        'Manual mode shares recognition and recording while retaining only thermostat heat control and manually selected light.',
      ],
      'scope_limits':[
        'These finite whole sessions cover the established behaviour classes and their selected combinations, not every permutation, unknown human behaviour or hardware defect.',
        'Sensor outages, transport failure, protection, persistence failure and interface permissions remain technical product/integration tests rather than fictitious human actions.',
        'No scenario claims a measured physical light fade merely from Home Assistant brightness feedback.',
      ],
      'families':[{ 'id':d['id'],'duration_s':d['duration_s'],'human_stays':len(d['stays']),'evaluations':len(d['expectations']),
                    'climate_file':d['measurement_file'],'climate_sha256':d['measurement_sha256']} for d in docs],
    }
    dump(HERE/'provenance.json',provenance)


if __name__=='__main__':main()
