import numpy as np
import pandas as pd

import gym_auv.utils.geomutils as geom
from gym_auv.objects.vessel import Vessel
from gym_auv.objects.path import RandomCurveThroughOrigin, Path
from gym_auv.objects.obstacles import PolygonObstacle, VesselObstacle
from gym_auv.environment import BaseEnvironment
from gym_auv.rendering.render3d import VIEW_DISTANCE_3D
import shapely.geometry, shapely.errors

import os 
dir_path = os.path.dirname(os.path.realpath(__file__))

UPDATE_WAIT = 100
TERRAIN_DATA_PATH = '../resources/terrain.npy'
INCLUDED_VESSELS = None

class RealWorldEnv(BaseEnvironment):

    def __init__(self, *args, **kw):
        self.last_scenario_load_coordinates = None
        self.all_terrain = None

        super().__init__(*args, **kw)

    def _generate(self):

        df = pd.read_csv(self.vessel_data_path)
        vessels = dict(tuple(df.groupby('Vessel_Name')))
        vessel_names = sorted(list(vessels.keys()))
        
        vessel_trajectories = []

        #print('Preprocessing traffic...')

        selected_vessels = self.rng.choice(vessel_names, min(len(vessel_names), self.n_vessels), replace=False)

        for vessel_name in selected_vessels:
            vessels[vessel_name]['AIS_Timestamp'] = pd.to_datetime(vessels[vessel_name]['AIS_Timestamp'])
            vessels[vessel_name]['AIS_Timestamp'] -= vessels[vessel_name].iloc[0]['AIS_Timestamp']
            start_timestamp = None

            last_timestamp = pd.to_timedelta(0, unit='D')
            cutoff_dt = pd.to_timedelta(0.1, unit='D')
            path = []
            for _, row in vessels[vessel_name].iterrows():
                if row['AIS_Length_Overall'] < 12:
                    continue
                if len(path) == 0:
                    start_timestamp = row['AIS_Timestamp']
                timedelta = row['AIS_Timestamp'] - last_timestamp
                if timedelta < cutoff_dt:
                    path.append((int((row['AIS_Timestamp']-start_timestamp).total_seconds()), (row['AIS_East']/10.0-self.x0, row['AIS_North']/10.0-self.y0)))
                else:
                    if len(path) > 1 and not np.isnan(row['AIS_Length_Overall']) and row['AIS_Length_Overall'] > 0:
                        start_index = self.rng.randint(0, len(path)-1)
                        vessel_trajectories.append((row['AIS_Length_Overall']/10.0, path[start_index:], vessel_name))
                    path = []
                last_timestamp = row['AIS_Timestamp']
        
            #if self.other_vessels:
            #    print(vessel_name, path[0], len(path))
        
        #print('Completed traffic preprocessing')

        other_vessel_indeces = self.rng.choice(list(range(len(vessel_trajectories))), min(len(vessel_trajectories), self.n_vessels), replace=False)
        self.other_vessels = [vessel_trajectories[idx] for idx in other_vessel_indeces]

        init_state = self.path(0)
        init_angle = self.path.get_direction(0)

        self.vessel = Vessel(self.config, np.hstack([init_state, init_angle]), width=self.config["vessel_width"])
        prog = self.path.get_closest_arclength(self.vessel.position)
        self.path_prog_hist = np.array([prog])
        self.max_path_prog = prog

        self.all_obstacles = []
        self.obstacles = []
        for obstacle_perimeter in self.obstacle_perimeters:
            if len(obstacle_perimeter) > 3:
                obstacle = PolygonObstacle(obstacle_perimeter)
                assert obstacle.boundary.is_valid, 'The added obstacle is invalid!'
                self.all_obstacles.append(obstacle)
                self.obstacles.append(obstacle)

        if self.verbose: print('Added {} obstacles'.format(len(self.obstacles)))

        if self.verbose: print('Generating {} vessel trajectories'.format(len(self.other_vessels)))
        for vessel_width, vessel_trajectory, vessel_name in self.other_vessels:
            # for k in range(0, len(vessel_trajectory)-1):
            #     vessel_obstacle = VesselObstacle(width=int(vessel_width), trajectory=vessel_trajectory[k:])
            #     self.all_obstacles.append(vessel_obstacle)
            if len(vessel_trajectory) > 2:
                vessel_obstacle = VesselObstacle(width=int(vessel_width), trajectory=vessel_trajectory, name=vessel_name)
                self.all_obstacles.append(vessel_obstacle)
                self.obstacles.append(vessel_obstacle)

        # if self.render_mode == '3d':
        #     if self.verbose:
        #         print('Loading nearby 3D terrain...')
        #     xlow = 0
        #     xhigh = self.all_terrain.shape[0]
        #     ylow = 0
        #     yhigh = self.all_terrain.shape[1]
        #     self._viewer3d.create_world(self.all_terrain, xlow, ylow, xhigh, yhigh)
        #     if self.verbose:
        #         print('Loaded nearby 3D terrain ({}-{}, {}-{})'.format(xlow, xhigh, ylow, yhigh))

        self._update()

    def _update(self):
        if self.render_mode == '3d':
            if self.t_step % UPDATE_WAIT == 0:
                travelled_distance = np.linalg.norm(self.vessel.position - self.last_scenario_load_coordinates) if self.last_scenario_load_coordinates is not None else np.inf
                if travelled_distance > VIEW_DISTANCE_3D/10:
                    if self.verbose:
                        print('Update scheduled with distance travelled {:.2f}.'.format(travelled_distance))
                    
                    if self.verbose:
                        print('Loading nearby terrain...'.format(len(self.obstacles)))
                    vessel_center = shapely.geometry.Point(
                        self.vessel.position[0], 
                        self.vessel.position[1],
                    )
                    self.obstacles = []
                    for obstacle in self.all_obstacles:
                        obst_dist = float(vessel_center.distance(obstacle.boundary)) - self.vessel.width
                        if obst_dist <= VIEW_DISTANCE_3D:
                            self.obstacles.append(obstacle)
                        else:
                            if not obstacle.static:
                                obstacle.update(UPDATE_WAIT*self.config["t_step_size"])

                    if self.verbose:
                        print('Loaded nearby terrain ({} obstacles).'.format(len(self.obstacles)))
                    
                    if self.verbose:
                        print('Loading nearby 3D terrain...')
                    x = int(self.vessel.position[0] + self.x0)
                    y = int(self.vessel.position[1] + self.y0)
                    xlow = max(0, x-VIEW_DISTANCE_3D)
                    xhigh = min(self.all_terrain.shape[0], x+VIEW_DISTANCE_3D)
                    ylow = max(0, y-VIEW_DISTANCE_3D)
                    yhigh = min(self.all_terrain.shape[1], y+VIEW_DISTANCE_3D)
                    self._viewer3d.create_world(self.all_terrain, xlow, ylow, xhigh, yhigh, self.x0, self.y0)
                    if self.verbose:
                        print('Loaded nearby 3D terrain ({}-{}, {}-{})'.format(xlow, xhigh, ylow, yhigh))

                    self.last_scenario_load_coordinates = self.vessel.position

        super()._update()

class Sorbuoya(RealWorldEnv):
    def __init__(self, *args, **kw):
        self.x0, self.y0 = 0, 10000
        self.vessel_data_path = '../resources/vessel_data_local_sorbuoya.csv'
        self.n_vessels = 25
        super().__init__(*args, **kw)

    def _generate(self):
        #self.path = Path([[-50, 1750], [250, 1200]])
        #self.path = Path([[650, 1750], [450, 1200]])
        self.path = Path([[1000, 830, 700, 960, 1080, 1125], [910, 800, 700, 550, 750, 810]])
        self.obstacle_perimeters = np.load('../resources/obstacles_sorbuoya.npy')
        self.all_terrain = np.load(TERRAIN_DATA_PATH)/7.5 #np.load(TERRAIN_DATA_PATH)[0000:2000, 10000:12000]/7.5
        super()._generate()

class Agdenes(RealWorldEnv):
    def __init__(self, *args, **kw):
        self.x0, self.y0 = 3121, 5890
        self.vessel_data_path = '../resources/vessel_data_local_agdenes.csv'
        self.n_vessels = 15
        super().__init__(*args, **kw)

    def _generate(self):
        #self.path = Path([[520, 1070, 4080, 5473, 10170, 12220], [3330, 5740, 7110, 4560, 7360, 11390]]) #South-west -> north-east
        self.path = Path([[4000-self.x0, 4247-self.x0, 4137-self.x0, 3937-self.x0, 3217-self.x0], [6500-self.y0, 6500-self.y0, 6860-self.y0, 6910-self.y0, 6690-self.y0]])
        self.obstacle_perimeters = np.load('../resources/obstacles_entrance.npy')
        self.all_terrain = np.load(TERRAIN_DATA_PATH)/7.5 #[3121:4521, 5890:7390]/7.5
        
        super()._generate()

class Trondheim(RealWorldEnv):
    def __init__(self, *args, **kw):
        self.x0, self.y0 = 5000,3900
        self.vessel_data_path = '../resources/vessel_data_local_trondheim.csv'
        self.n_vessels = 100
        super().__init__(*args, **kw)

    def _generate(self):
        self.path = Path([[6945-self.x0, 6329-self.x0], [4254-self.y0, 5614-self.y0]])
        self.obstacle_perimeters = np.load('../resources/obstacles_trondheim.npy')
        self.all_terrain = np.load(TERRAIN_DATA_PATH)[self.x0:8000, self.y0:6900]/7.5
        super()._generate()

class Trondheimsfjorden(RealWorldEnv):
    def __init__(self, *args, **kw):
        self.x0, self.y0 = 0, 0
        self.vessel_data_path = '../resources/vessel_data.csv'
        self.n_vessels = 0
        super().__init__(*args, **kw)

    def _generate(self):
        self.path = Path([[520, 1070, 4080, 5473, 10170, 12220], [3330, 5740, 7110, 4560, 7360, 11390]]) #South-west -> north-east
        self.obstacle_perimeters = np.load('../resources/obstacles_trondheimsfjorden.npy')
        self.all_terrain = np.load(TERRAIN_DATA_PATH)/7.5 #[3121:4521, 5890:7390]/7.5
        
        super()._generate()
