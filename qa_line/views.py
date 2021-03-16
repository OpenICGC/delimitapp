# -*- coding: utf-8 -*-

# ----------------------------------------------------------
# TERRITORIAL DELIMITATION TOOLS (ICGC)
# Authors: Cesc Masdeu & Fran Martin
# Version: 1.0
# Date: 20210315
# Version Python: 3.7
# ----------------------------------------------------------

"""
Quality check of a line ready to upload to the database
"""

# Standard library imports
import os
import os.path as path
from datetime import datetime
import logging
import re

# Third party imports
import geopandas as gpd
from osgeo import gdal
from django.views import View
from django.shortcuts import render, redirect
from django.contrib import messages

# Local imports
from qa_line.config import *
from DelimitAPI.common.utils import line_id_2_txt


class CheckQualityLine(View):
    """
    Class for checking a line's geometry and attributes quality previously to upload it into the database
    """
    # Workspace parameters
    line_id = None
    line_id_txt = None
    current_date = datetime.now().strftime("%Y%m%d-%H%M")
    logger = logging.getLogger()
    log_path = None
    ppf_list = None
    founded_points_dict = None
    # Paths to directories and folders
    line_folder = None
    doc_delim = None
    carto_folder = None
    tables_folder = None
    photo_folder = None
    # Geodataframes for data managing
    tram_line_mem_gdf = None
    fita_mem_gdf = None
    lin_tram_ppta_line_gdf = None
    punt_line_gdf = None
    p_proposta_df = None
    punt_fit_df = None
    # Coordinates data structures
    points_coords_dict = None
    line_coords_list = None
    # Json response
    response_data = {}

    def get(self, request):
        """
        Main entry point. Here is where the magic is done. This method is called when someone wants to init the process of quality
        checking and is supposed to prepare the workspace, prepare the line and check it's geometry and attributes
        """
        # SET UP THE WORKING ENVIRONMENT -------------------------
        # Set up parameters
        line_id = request.GET.get('line_id')
        if not line_id:
            msg = "No s'ha introduït cap ID Linia."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        self.set_up(line_id)
        # Check that the upload line directory exists
        line_dir_exists = self.check_line_dir_exists()
        if not line_dir_exists:
            msg = f"No existeix la carpeta de la linia {self.line_id} al directori de càrrega."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Check and set directories paths
        directory_tree_valid = self.check_directories()
        if directory_tree_valid:
            self.set_directories()
        else:
            msg = "L'estructura de directoris de la carpeta de la linia no és vàlida. Si us plau, revisa" \
                  " que la carpeta DocDelim existeixi i que tots els seus subdirectoris també."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Remove temp files from the workspace
        try:
            self.rm_temp()
        except Exception as e:
            msg = f'Error esborrant arxius temporals => {e}'
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Check if all the necessary entities exist
        entities_exist = self.check_entities_exist()
        if not entities_exist:
            msg = "Falten capes o taules necessaries pel procés de control de qualitat. Si us plau, revisa que totes les capes i taules" \
                  " estiguin a les carpetes 'Cartografia' i 'Taules'."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Copy layers and tables from line's folder to the workspace
        copied_data_ok = self.copy_data_2_gpkg()
        if not copied_data_ok:
            msg = "No s'han pogut copiar capes o taules. Veure log per més informació."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Set the layers geodataframe
        self.set_layers_gdf()
        # Create list with only points that are "Proposta Final"
        self.ppf_list = self.get_ppf_list()
        # Create dict with the only points that are founded and PPF
        self.founded_points_dict = self.get_founded_points_dict()
        # Get a dict with the points ID and their coordinates
        self.points_coords_dict = self.get_round_point_coordinates()
        # Get a list with the line coordinates
        self.line_coords_list = self.get_round_line_coordinates()

        # START CHECKING ------------------------------------------
        # Check if the line ID already exists into the database
        self.check_line_id_exists()
        # Check if the line's field structure and content is correct
        lin_tram_ppta_ok = self.check_lin_tram_ppta_layer()
        if not lin_tram_ppta_ok:
            msg = "L'estructura de camps de Lin_TramPpta.shp no és correcte i no es pot continuar el procés, donat que hi ha algun" \
                  " camp que falta o sobra a la capa. Si us plau, revisa-la."
            response = self.create_error_response(msg)
            return render(request, '../templates/qa_reports.html', response)
        # Check the line and point's geometry
        self.check_layers_geometry()
        # Get info from the parts and vertexs of every line tram
        self.info_vertexs_line()
        # Check that the points are correctly rounded
        self.check_points_decimals()
        # Get info and check the features in P_Proposta
        self.info_p_proposta()
        # Check some aspects about founded points
        if self.founded_points_dict:   # Check if exists any founded point
            self.check_found_points()
        # Check that the 3T points are informed correctly
        self.check_3termes()
        # Check the relation between the tables and the point layer
        self.check_relation_points_tables()
        # Check the topology in order to avoid topological errors
        self.check_topology()

        # Send response as OK
        self.response_data['result'] = 'OK'
        self.response_data['message'] = f'Linia {line_id} checkejada'
        response = self.add_response_data()
        return render(request, '../templates/qa_reports.html', response)

    def set_up(self, line_id):
        """
        Set up the environment parameters that the class would need
        :param line_id: line ID from the line the class is going to check
        """
        # Set line ID
        self.line_id = line_id
        # Convert line ID from integer to string nnnn
        self.line_id_txt = line_id_2_txt(self.line_id)
        # Configure logger
        self.set_logging_config()
        # Write first log message
        self.write_first_report()

    def set_logging_config(self):
        """
        Set up the logger config
        """
        # Logging level
        self.logger.setLevel(logging.INFO)
        # Message format
        log_format = logging.Formatter("%(levelname)s - %(message)s")
        # Log filename and path
        log_name = f"QA-Report_{self.line_id_txt}_{self.current_date}.txt"
        self.log_path = path.join(LINES_DIR, self.line_id, WORK_REC_DIR, log_name)
        if path.exists(self.log_path):   # If a log with the same filename exists, removes it
            os.remove(self.log_path)
        file_handler = logging.FileHandler(filename=self.log_path, mode='w')
        file_handler.setFormatter(log_format)
        self.logger.addHandler(file_handler)

    def check_line_dir_exists(self):
        """
        Check if the line folder exists in the uploading directory
        :return: boolean that indicates whether the line folder exists or not
        """
        line_folder = os.path.join(UPLOAD_DIR, str(self.line_id))
        if path.exists(line_folder):
            self.line_folder = line_folder
            return True
        else:
            return False

    def check_directories(self):
        """
        Check if the directory tree structure and content is correct
        :return: boolean that indicates if the line's folder's directory tree is correct or not
        """
        tree_valid = True

        doc_delim = os.path.join(self.line_folder, 'DocDelim')
        if path.exists(doc_delim):  # Check the DocDelim folder exists
            self.doc_delim = doc_delim
            for sub_dir in SUB_DIR_LIST:  # Check if all the subdirs exist
                if sub_dir not in os.listdir(self.doc_delim):
                    tree_valid = False
                    self.logger.error(f'No existeix el subdirectori {sub_dir}')
        else:
            self.logger.error('No existeix DocDelim dins el directori de la linia')
            tree_valid = False

        if tree_valid:
            self.logger.info('Estructura de directoris OK')
            return tree_valid

    def set_directories(self):
        """Set paths to directories"""
        self.carto_folder = os.path.join(self.doc_delim, 'Cartografia')
        self.tables_folder = os.path.join(self.doc_delim, 'Taules')
        self.photo_folder = os.path.join(self.doc_delim, 'Fotografies')

    def set_layers_gdf(self):
        """Open all the necessary layers as geodataframes with geopandas"""
        # SIDM2
        self.tram_line_mem_gdf = gpd.read_file(WORK_GPKG, layer='tram_linia_mem')
        self.fita_mem_gdf = gpd.read_file(WORK_GPKG, layer='fita_mem')
        # Line
        self.lin_tram_ppta_line_gdf = gpd.read_file(WORK_GPKG, layer='Lin_TramPpta')
        self.punt_line_gdf = gpd.read_file(WORK_GPKG, layer='Punt')
        # Tables
        self.p_proposta_df = gpd.read_file(WORK_GPKG, layer='P_Proposta')
        self.punt_fit_df = gpd.read_file(WORK_GPKG, layer='PUNT_FIT')

    def check_entities_exist(self):
        """
        Check if all the necessary shapefiles and tables exists
        :return: entities_exist - Boolean that means if all the entities exists in the source workspace
        """
        shapes_exist, tables_exist = False, False
        for shape in SHAPES_LIST:
            shape_path = os.path.join(self.carto_folder, shape)
            if path.exists(shape_path):
                shapes_exist = True
            else:
                shapes_exist = False

        for dbf in TABLE_LIST:
            dbf_path = os.path.join(self.tables_folder, dbf)
            if path.exists(dbf_path):
                tables_exist = True
            else:
                tables_exist = False

        if not shapes_exist and not tables_exist:
            self.logger.error('Falten capes i taules necessàries a les carpetes de Cartografia i Taules')
            return False
        elif not shapes_exist and tables_exist:
            self.logger.error('Falten capes necessàries a la carpeta de Cartografia')
            return False
        elif shapes_exist and not tables_exist:
            self.logger.error('Falten taules necessàries a la carpeta de Taules')
            return False
        else:
            return True

    def copy_data_2_gpkg(self):
        """Copy all the feature classes and tables from the line's folder to the local work geopackage"""
        for shape in SHAPES_LIST:
            shape_name = shape.split('.')[0]
            shape_path = os.path.join(self.carto_folder, shape)
            try:
                shape_gdf = gpd.read_file(shape_path)
                shape_gdf.to_file(WORK_GPKG, layer=shape_name, driver="GPKG")
            except Exception as e:
                self.logger.error(f"No s'ha pogut copiar la capa {shape_name} => {e}.")
                return False

        for dbf in TABLE_LIST:
            dbf_name = dbf.split('.')[0]
            dbf_path = os.path.join(self.tables_folder, dbf)
            try:
                dbf_gdf = gpd.read_file(dbf_path)
                dbf_gdf.to_file(WORK_GPKG, layer=dbf_name, driver="GPKG")
            except Exception as e:
                self.logger.error(f"No s'ha pogut copiar la taula {dbf_name} => {e}")
                return False

        self.logger.info(f"Capes i taules de la linia {self.line_id} copiades correctament a CQline.gpkg")
        return True

    def check_line_id_exists(self):
        """Check if the line ID already exists into the database, both into fita_mem and lin_tram_mem"""
        line_id_in_lin_tram = False
        line_id_in_fita_g = False

        # Check into lin_tram_mem
        tram_duplicated_id = self.tram_line_mem_gdf['id_linia'] == int(self.line_id)
        tram_line_id = self.tram_line_mem_gdf[tram_duplicated_id]
        if not tram_line_id.empty:
            line_id_in_lin_tram = True

        # Check into fita_mem
        fita_duplicated_id = self.fita_mem_gdf['id_linia'] == int(self.line_id)
        fita_line_id = self.fita_mem_gdf[fita_duplicated_id]
        if not fita_line_id.empty:
            line_id_in_fita_g = True

        if line_id_in_fita_g and line_id_in_lin_tram:
            self.logger.error("L'ID Linia introduït està en fita_mem i tram_linia_mem")
        elif line_id_in_fita_g and not line_id_in_lin_tram:
            self.logger.error("L'ID Linia introduït està en fita_mem però no en lin_tram_mem")
        elif not line_id_in_fita_g and line_id_in_lin_tram:
            self.logger.error("L'ID Linia introduït no està en fita_mem però sí en lin_tram_mem")
        elif not line_id_in_fita_g and not line_id_in_lin_tram:
            self.logger.info("L'ID Linia no està repetit a fita_mem i lin_tram_mem de SIDM3")

    def check_lin_tram_ppta_layer(self):
        """Check line's layer's field structure and content"""
        # The line's layer's field structure is critic for the correct running of the QA process. If it's not correct,
        # the process must stop
        fields_lin_tram_ppta_ok = self.check_fields_lin_tram_ppta()
        if not fields_lin_tram_ppta_ok:
            return False
        self.check_fields_content_lint_tram_ppta()

        return True

    def check_fields_lin_tram_ppta(self):
        """Check line's layer's field structure is correct"""
        # Fields that the line's layer must have
        true_fields = ('ID_LINIA', 'ID', 'DATA', 'COMENTARI', 'P1', 'P2', 'P3', 'P4', 'PF',
                       'ID_FITA1', 'ID_FITA2', 'geometry')

        # Get line's layer's fields
        lin_tram_ppta_line_gdf = gpd.read_file(WORK_GPKG, layer='Lin_TramPpta')
        lin_tram_fields = list(lin_tram_ppta_line_gdf.columns)

        # Compare
        field_match = 0
        for field in lin_tram_fields:
            if field in true_fields:
                field_match += 1

        if field_match == len(true_fields):
            self.logger.info("L'estructura de camps de Lin_TramPpta és correcte")
            return True
        else:
            self.logger.error("L'estructura de camps de Lin_TramPpta NO és correcte")
            return False

    def check_fields_content_lint_tram_ppta(self):
        """Check line's layer's content is correct"""
        # Check that doesn't exist the line ID from another line
        line_id_error = False
        not_line_id = self.lin_tram_ppta_line_gdf['ID_LINIA'] != int(self.line_id)
        tram_not_line_id = self.lin_tram_ppta_line_gdf[not_line_id]
        if not tram_not_line_id.empty:
            line_id_error = True
            self.logger.error("Existeixen trams de linia amb l'ID_LINIA d'una altra linia")

        # Check that the fita ID is correct
        id_fita_error = False
        # F1
        id_f1_bad = self.lin_tram_ppta_line_gdf['ID_FITA1'] == '1'
        tram_id_f1_bad = self.lin_tram_ppta_line_gdf[id_f1_bad]
        # F2
        id_f2_bad = self.lin_tram_ppta_line_gdf['ID_FITA2'] == '1'
        tram_id_f2_bad = self.lin_tram_ppta_line_gdf[id_f2_bad]

        if not tram_id_f1_bad.empty or not tram_id_f2_bad.empty:
            id_fita_error = True
            self.logger.error("El camp ID_FITA d'algun dels trams de la linia no és vàlid")

        if not line_id_error and not id_fita_error:
            self.logger.info("Els camps de Lin_TramPpta estan correctament emplenats")

    def get_ppf_list(self):
        """
        Get dataframe with the ID of the only points that are "Punt Proposta Final", as known as "PPF"
        :return: ppf_list - List with the ID of the PPF
        """
        is_ppf = self.p_proposta_df['PFF'] == 1
        ppf_df = self.p_proposta_df[is_ppf]
        ppf_list = ppf_df['ID_PUNT'].to_list()

        return ppf_list

    def get_founded_points_dict(self):
        """
        Get a dict of the founded PPF points with etiqueta as key and ID_PUNT as value
        :return: points_founded_dict - Dict of the founded points with the key, value -> ID_FITA, ID_Punt
        """
        points_founded_dict = {}
        founded = self.punt_fit_df['TROBADA'] == '1'
        points_founded = self.punt_fit_df[founded]
        if not points_founded.empty:
            for index, feature in points_founded.iterrows():
                if feature['ID_PUNT'] in self.ppf_list:
                    point_id = feature['ID_PUNT']
                    point_num = feature['ID_FITA']
                    points_founded_dict[point_num] = point_id

            return points_founded_dict

        else:
            return False

    def check_layers_geometry(self):
        """Check the geometry of both line and points"""
        self.logger.info('Comprovació de les geometries...')
        self.logger.info('Lin_Tram_Proposta :')
        self.check_lin_tram_geometry()
        self.logger.info('Punt :')
        self.check_points_geometry()

    def check_lin_tram_geometry(self):
        """Check the line's geometry"""
        # Check if there are empty features
        is_empty = self.lin_tram_ppta_line_gdf.is_empty
        empty_features = self.lin_tram_ppta_line_gdf[is_empty]
        if not empty_features.empty:
            for index, feature in empty_features.iterrows():
                tram_id = feature['ID']
                self.logger.error(f'      El tram {tram_id} està buit')
        # Check if there is a ring
        is_ring = self.lin_tram_ppta_line_gdf.is_empty
        ring_features = self.lin_tram_ppta_line_gdf[is_ring]
        if not ring_features.empty:
            for index, feature in ring_features.iterrows():
                tram_id = feature['ID']
                self.logger.error(f'      El tram {tram_id} té un anell interior')
        # Check if the line is multi-part and count the parts in that case
        not_multipart = True
        for index, feature in self.lin_tram_ppta_line_gdf.iterrows():
            geom_type = feature['geometry'].geom_type
            if geom_type == 'MultiLineString':
                not_multipart = False
                tram_id = feature['ID']
                n_parts = feature['geometry'].geoms
                self.logger.error(f'      El tram {tram_id} és multi-part i té {n_parts} parts')

        if empty_features.empty and ring_features.empty and not_multipart:
            self.logger.info("      No s'ha detectat cap error de geometria a Lin_TramPpta")

    def check_points_geometry(self):
        """Check the points' geometry"""
        # Check if there are empty features
        is_empty = self.punt_line_gdf.is_empty
        empty_features = self.punt_line_gdf[is_empty]
        if not empty_features.empty:
            for index, feature in empty_features.iterrows():
                point_id = feature['ID_PUNT']
                self.logger.error(f'      El punt {point_id} està buit')
        # Check if the geometry is valid
        is_valid = self.punt_line_gdf.is_valid
        invalid_features = self.punt_line_gdf[~is_valid]
        if not invalid_features.empty:
            for index, feature in invalid_features.iterrows():
                point_id = feature['ID_PUNT']
                self.logger.error(f'      El punt {point_id} no té una geometria vàlida')

        if empty_features.empty and invalid_features.empty:
            self.logger.info("      No s'ha detectat cap error de geometria a la capa Punt")

    def info_vertexs_line(self):
        """Get info and make a recount of the line's vertexs"""
        self.logger.info('Vèrtexs de la linia :')
        for index, feature in self.lin_tram_ppta_line_gdf.iterrows():
            tram_id = feature['ID']
            tram_vertexs = len(feature['geometry'].coords)   # Nº of vertexs that compose the tram
            self.logger.info(f"      Tram ID : {tram_id}  |  Nº vèrtex : {tram_vertexs}")

    def check_points_decimals(self):
        """Check if the points's decimals are correct and are rounded to 1 decimal"""
        decim_valid = True
        for index, feature in self.punt_line_gdf.iterrows():
            # Point parameters
            '''
            Due the add respone function converts all the logger's messages into a JSON splitting the reports by "-"
            is mandatory to avoid using that character in the reports in order to don't lose data adding the messages
            to the JSON.
            '''
            if feature['ID_PUNT'] in self.ppf_list:   # Check if the point is a ppf point
                point_num = feature['ETIQUETA'].split('-')[-1]
                point_id = feature['ID_PUNT'].split('-')[-1]
                point_x = feature['geometry'].x
                point_y = feature['geometry'].y
                # Check if rounded correctly
                dif_x = abs(point_x - round(point_x, 1))
                dif_y = abs(point_y - round(point_y, 1))
                if dif_x > 0.01 or dif_y > 0.01:
                    decim_valid = False
                    self.logger.error(f"La fita {point_num}  |  ID_PUNT : {point_id} no està correctament decimetritzada")

        if decim_valid:
            self.logger.info('Les fites estan correctament decimetritzades')

    def info_p_proposta(self):
        """
        Get info and check the points in the table P_Proposta, like:
            - Count the points and distinguish them depending on its type
            - Check that the field ORDPF is not NULL
            - Check that an auxiliary point is not indicated as a real point
        """
        self.logger.info('Informació de les fites :')
        # Count the points in the table P_Proposta depending on the point's type
        self.count_points()
        # Check that ORDPF is not null
        ordpf_valid = self.check_ordpf()
        # Check that the points in P_Proposta are correctly indicated
        real_points_valid = self.check_real_points()
        if ordpf_valid and real_points_valid:
            self.logger.info(f'Tots els registre de la taula P_Proposta són vàlids.')

    def count_points(self):
        """Count the points in the table P_Proposta and distinguish them depending on its type"""
        # Not final points
        # PFF = 0
        not_ppf = self.p_proposta_df['PFF'] == 0
        not_final_points = self.p_proposta_df[not_ppf]
        n_not_final_points = not_final_points.shape[0]
        # Real points
        # PFF = 1 AND ESFITA = 1
        proposta_points = self.p_proposta_df.loc[(self.p_proposta_df['PFF'] == 1) & (self.p_proposta_df['ESFITA'] == 1)]
        n_proposta_points = proposta_points.shape[0]
        # Auxiliary points
        # PFF = 1 AND ESFITA = 0
        auxiliary_points = self.p_proposta_df.loc[(self.p_proposta_df['PFF'] == 1) & (self.p_proposta_df['ESFITA'] == 0)]
        n_auxiliary_points = auxiliary_points.shape[0]

        self.logger.info(f'      Fites PPF reals: {n_proposta_points}')
        self.logger.info(f'      Fites PPF auxiliars: {n_auxiliary_points}')
        self.logger.info(f'      Fites no finals: {n_not_final_points}')

    def check_ordpf(self):
        """
        Check the field ORDPF is not NULL
        :return valid - Boolean that means if the ORDPF field is OK
        """
        valid = True
        ordpf_null = self.p_proposta_df['ORDPF'].isnull()
        points_ordpf_null = self.p_proposta_df[ordpf_null]

        if not points_ordpf_null.empty:
            valid = False
            for index, feature in points_ordpf_null.iterrows():
                point_id = feature['ID_PUNT'].split('-')[-1]
                self.logger.error(f"      El camp ORDPF del punt {point_id} a la taula PUNT_FIT és nul.")

        return valid

    def check_real_points(self):
        """Check that an auxiliary point is not indicated as a real point"""
        # If PPF = 1 and ORDPF = 0 => ESFITA MUST BE 0
        valid = True
        bad_auxiliary_points = self.p_proposta_df.loc[(self.p_proposta_df['PFF'] == 1) &
                                                      (self.p_proposta_df['ORDPF'] == 0) &
                                                      (self.p_proposta_df['ESFITA'] != 0)]

        if not bad_auxiliary_points.empty:
            valid = False
            for index, feature in bad_auxiliary_points.iterrows():
                point_id = feature['ID_PUNT'].split('-')[-1]
                self.logger.error(f"      El punt amb ID PUNT : {point_id} està mal indicat a P_Proposta: sembla que es tracta "
                                  f"d'una fita auxiliar indicada com a fita real")

        return valid

    def check_found_points(self):
        """
        Check diferent things about the founded points, like:
            - Has photography
            - The photography exists in its folder
            - If the point has Z coordinate must be founded point
        """
        # Check that the point has a photography indicated
        self.check_photo_exists()
        # Check that the photography exists in the photo's folder
        self.check_photo_name()
        # Check that if the point has Z coordinate is a founded point
        self.check_cota_fita()

    def check_photo_exists(self):
        """Check that a founded point has a photography"""
        # Get a list of points with photography
        photo_exists = self.punt_line_gdf['FOTOS'].notnull()
        points_with_photo = self.punt_line_gdf[photo_exists]
        points_with_photo_list = points_with_photo['ID_PUNT'].to_list()
        # Only points that are PPF
        ppf_with_photo_list = [point_id for point_id in points_with_photo_list if point_id in self.ppf_list]
        # Get a dict with the founded points without photography
        founded_points_no_photo = {etiqueta: id_punt for (etiqueta, id_punt) in self.founded_points_dict.items() if id_punt not in ppf_with_photo_list}

        if not founded_points_no_photo:
            self.logger.info('Totes les fites trobades tenen fotografia.')
        else:
            for etiqueta, point_id in founded_points_no_photo.items():
                etiqueta = etiqueta.split('-')[-1]
                point_id = point_id.split('-')[-1]
                self.logger.error(f'      La fita {etiqueta}  |  ID PUNT : {point_id} és trobada però no té cap fotografia indicada.')

    def check_photo_name(self):
        """Check that the photography in the layer has the same name as de .JPG file"""
        # Get a list with the photographies's filename in the photography folder
        folder_photos_filenames = [f for f in os.listdir(self.photo_folder) if os.path.isfile(os.path.join(self.photo_folder, f)) and
                                   (f.endswith(".jpg") or f.endswith(".JPG"))]
        # Get a list with the photographies's filename from PPF
        photo_exists = self.punt_line_gdf['FOTOS'].notnull()
        points_with_photo = self.punt_line_gdf[photo_exists]
        founded_points_photos = [feature['FOTOS'] for index, feature in points_with_photo.iterrows() if feature['ID_PUNT'] in self.ppf_list]
        # Check that the photography in the point layer has the same filename as the photography into the folder
        photos_valid = True
        for photo_filename in founded_points_photos:
            if photo_filename not in folder_photos_filenames:
                photos_valid = False
                self.logger.error(f'La fotografia {photo_filename} no està a la carpeta de Fotografies.')

        if photos_valid:
            self.logger.info('Totes les fotografies informades a la capa Punt estan a la carpeta de Fotografies.')

    def check_cota_fita(self):
        """Check that a point with Z coordinate is founded"""
        # Get a list with the PPF that have Z coordinate
        ppf_z_dict = {}
        for index, feature in self.punt_line_gdf.iterrows():
            etiqueta = feature['ETIQUETA']
            point_id = feature['ID_PUNT']
            z_coord = feature['geometry'].z
            if z_coord > 0 and point_id in self.ppf_list:
                ppf_z_dict[etiqueta] = point_id

        z_coord_valid = True
        for etiqueta, point_id in ppf_z_dict.items():
            if point_id not in self.founded_points_dict.values():
                z_coord_valid = False
                etiqueta = etiqueta.split('-')[-1]
                point_id = point_id.split('-')[-1]
                self.logger.error(f'La F {etiqueta}  |  ID PUNT : {point_id} té coordenada Z però no és fita trobada.')

        if z_coord_valid:
            self.logger.info('Totes les fites amb coordenada Z són trobades')

    def check_3termes(self):
        """Check 3 terms points"""
        # Get df with points sorted by etiqueta, firstly creating a new sorting column that containts the numbers
        # from the ETIQUETA field as integers, in order to correctly sort the point numbers
        sorted_points_df_temp = self.punt_line_gdf
        etiquetes = sorted_points_df_temp['ETIQUETA'].tolist()
        etiquetes_int = []
        for i in etiquetes:
            point_num = int(re.search(r'\d+', i).group())
            etiquetes_int.append(point_num)
        # Add sorting column
        sorted_points_df_temp['sorting'] = etiquetes_int
        # Sort by the new column
        sorted_points_df = sorted_points_df_temp.sort_values(by=['sorting'])
        # Filter by ppf points
        ppf_point = sorted_points_df['ID_PUNT'].isin(self.ppf_list)
        sorted_points_df = sorted_points_df[ppf_point]
        # Get a list with the contact field from both first and last point
        first_point = sorted_points_df[sorted_points_df.ID_PUNT.isin(self.ppf_list)].iloc[0]
        last_point = sorted_points_df[sorted_points_df.ID_PUNT.isin(self.ppf_list)].iloc[-1]
        if first_point['CONTACTE'] and last_point['CONTACTE']:
            self.logger.info('Les fites 3T tenen informat el camp CONTACTE.')
        else:
            self.logger.error('Hi ha fites 3T que no tenen informat el camp CONTACTE.')

        # Recount how many points have the CONTACTE field not empty
        informed_3t_points = self.punt_line_gdf[self.punt_line_gdf['CONTACTE'].notnull()]
        n_informed_3t_points = informed_3t_points.shape[0]
        self.logger.info(f'Hi ha un total de {n_informed_3t_points} fites amb el camp CONTACTE informat.')

    def check_relation_points_tables(self):
        """Check that all the points that exist in the tables exist in the point layer"""
        self.logger.info('Comprovant correspondència entre les taules i la capa Punt...')
        points_id_list = self.punt_line_gdf['ID_PUNT'].tolist()

        # Check that all the ID_PUNT from P_Proposta exist in the point layer
        p_proposta_valid = True
        for index, feature in self.p_proposta_df.iterrows():
            point_id = feature['ID_PUNT']
            if point_id not in points_id_list:
                p_proposta_valid = False
                point_id = point_id.split('-')[-1]
                self.logger.error(f'El registre amb ID PUNT : {point_id} de la taula P_PROPOSTA no està a la capa Punt.')
        if p_proposta_valid:
            self.logger.info('      Correspondència OK entre els punts de P_PROPOSTA i Punt.')

        # Check that all the ID_PUNT from PUNT_FIT exist in the point layer
        punt_fit_valid = True
        for index_, feature_ in self.punt_fit_df.iterrows():
            point_id_ = feature_['ID_PUNT']
            if point_id_ not in points_id_list:
                punt_fit_valid = False
                point_id_ = point_id.split('-')[-1]
                self.logger.error(f'El registre amb ID PUNT : {point_id_} de la taula PUNT_FIT no està a la capa Punt.')
        if punt_fit_valid:
            self.logger.info('      Correspondència OK entre els punts de PUNT_FIT i Punt.')

    def check_topology(self):
        """Check topology"""
        self.logger.info('Iniciant controls topològics...')
        # Check that the line doesn't crosses or overlaps itself
        self.check_line_crosses_itself()
        # Check that the line doesn't intersect the db lines
        self.check_line_intersects_db()
        # Check that hte line doesn't overlaps the db lines
        self.check_line_overlaps_db()
        # Check that the lines endpoints are equal to any point
        self.check_endpoint_covered_point()
        # Check that if a point is not over a line is because it's an auxiliary point
        self.check_auxiliary_point()

    def check_line_crosses_itself(self):
        """
        Check that the line doesn't intersects or touches itself. In order to do that, iterates over
        every line's geometry and checks if it crosses any of the other lines
        """
        valid = True
        # Check if some tram does self-intersect
        tram_is_valid = self.lin_tram_ppta_line_gdf.is_valid
        invalid_features = self.lin_tram_ppta_line_gdf[~tram_is_valid]
        if not invalid_features.empty:
            valid = False
            for i, invalid_feature in invalid_features.iterrows():
                tram_id = invalid_feature['ID']
                self.logger.error(f"      El tram {tram_id} de la línia s'intersecta o toca a sí mateix.")
        # Check if some tram intersects another line's tram
        for i, tram in self.lin_tram_ppta_line_gdf.iterrows():
            line = self.lin_tram_ppta_line_gdf[self.lin_tram_ppta_line_gdf['geometry'] != tram['geometry']]
            # Iterates over the other line's trams to check if the curren tram crosses the others
            for i_, tram_ in line.iterrows():
                if tram['geometry'].crosses(tram_['geometry']):
                    valid = False
                    self.logger.error(f'      El tram {tram["ID"]} de la línia talla el '
                          f'tram {tram_["ID"]} de la mateixa línia.')
        if valid:
            self.logger.info("      Els trams de la linia no s'intersecten o toquen a sí mateixos.")

    def check_line_intersects_db(self):
        """Check that the line doesn't intersects or crosses the database lines"""
        features_intersects_db = gpd.sjoin(self.lin_tram_ppta_line_gdf, self.tram_line_mem_gdf, op='contains')
        if not features_intersects_db.empty:
            for index, feature in features_intersects_db.iterrows():
                self.logger.error(f"      El tram {feature['ID']} de la línia talla algun tram de la base de dades.")
        else:
            self.logger.info('      Els trams de la linia no intersecten cap tram de la base de dades.')

    def check_line_overlaps_db(self):
        """Check that the line doesn't overlaps the database lines"""
        features_overlaps_db = gpd.sjoin(self.lin_tram_ppta_line_gdf, self.tram_line_mem_gdf, op='contains')
        if not features_overlaps_db.empty:
            for index, feature in features_overlaps_db.iterrows():
                self.logger.error(f"      El tram {feature['ID']} de la línia es sobreposa a algun tram de la base de dades.")
        else:
            self.logger.info('      Els trams de la linia no es sobreposen a cap tram de la base de dades.')

    def check_endpoint_covered_point(self):
        """Check that the coordinates of the lines endpoints are equal to any point"""
        # Check if the lines endpoints coordinates are equal to any point
        endpoint_covered = True
        for index, feature in self.lin_tram_ppta_line_gdf.iterrows():
            tram_id = feature['ID']
            # Get and round endpoints coordinates
            first_endpoint_no_rounded = feature['geometry'].coords[0]
            first_endpoint = (round(first_endpoint_no_rounded[0], 1), round(first_endpoint_no_rounded[1], 1))
            last_endpoint_no_rounded = feature['geometry'].coords[-1]
            last_endpoint = (round(last_endpoint_no_rounded[0], 1), round(last_endpoint_no_rounded[1], 1))

            if (first_endpoint or last_endpoint) not in self.points_coords_dict.values():
                endpoint_covered = False
                self.logger.error(f'      Algun dels punts finals del tram {tram_id} no coincideixen amb una fita de la capa Punt.')

        if endpoint_covered:
            self.logger.info('      Tots els punts finals dels trams de la linia coincideixen amb una fita de la capa Punt.')

    def get_round_point_coordinates(self):
        """
        Get a dict of the points ID and coordinates, and round them to 1 decimal
        :return: points_coord_dict - Dict of points coordinates with format (x, y)
        """
        # Get ID
        points_id = self.punt_line_gdf['ID_PUNT'].tolist()
        # Get coordinates
        x_coords_no_round_list = self.punt_line_gdf['geometry'].x.tolist()
        x_coords_list = [round(x, 1) for x in x_coords_no_round_list]
        y_coords_no_round_list = self.punt_line_gdf['geometry'].y.tolist()
        y_coords_list = [round(y, 1) for y in y_coords_no_round_list]
        # Get list with the points coordinates
        points_coord_list = list(zip(x_coords_list, y_coords_list))
        # Enrich list with the point id and convert to dict
        points_coord_dict = dict(zip(points_id, points_coord_list))

        return points_coord_dict

    def check_auxiliary_point(self):
        """
        Check if a point that is not covered by the line is an auxiliary point
        """
        # Check if the point is covered by the line
        for point_id, point_coords in self.points_coords_dict.items():
            if point_coords not in self.line_coords_list:   # Check if the point coordinates are not covered by the line
                if point_id in self.ppf_list:   # Check if the point is a ppf point
                    point = self.punt_fit_df.loc[self.punt_fit_df['ID_PUNT'] == point_id]
                    aux = point['AUX'].values[0]
                    n_fita = point['ID_FITA'].values[0]
                    point_id = point_id.split('-')[-1]
                    if aux == '1':
                        self.logger.info(f'      La fita F {n_fita} | ID PUNT {point_id} no està a sobre de la linia però és auxiliar.')
                    else:
                        self.logger.error(f'      La fita F {n_fita} | ID PUNT {point_id} no està a sobre de la linia i NO és auxiliar.')

    def get_round_line_coordinates(self):
        """
        Get a list with the line's coordinates and rount them to 1 decimal
        :return: line_coords_list - List with the line's coordinates
        """
        line_coords_no_rounded = []
        trams = self.lin_tram_ppta_line_gdf['geometry'].tolist()
        for t in trams:
            tram_coords = t.coords
            for v in tram_coords:
                line_coords_no_rounded.append(v)
        line_coords_list = [(round(x, 1), round(y, 1)) for x, y in line_coords_no_rounded]

        return line_coords_list

    def write_first_report(self):
        """Write first log's report"""
        init_log_report = f"ID Linia : {self.line_id_txt} |  Data i hora CQ : {self.current_date}"
        self.logger.info(init_log_report)

    def rm_temp(self):
        """Remove temporal files from the workspace"""
        gpkg = gdal.OpenEx(WORK_GPKG, gdal.OF_UPDATE, allowed_drivers=['GPKG'])
        for layer in TEMP_ENTITIES:
            layer_name = layer.split('.')[0]
            gpkg.ExecuteSQL(f'DROP TABLE {layer_name}')

        self.logger.info('Arxius temporals esborrats')

    def create_error_response(self, message):
        """Create a error JSON for the response data"""
        self.logger.error(message)
        self.response_data['result'] = 'error'
        self.response_data['message'] = message
        logging.shutdown()

        return {'response': self.response_data}

    def add_response_data(self):
        """Add the log's reports to the JSON response data"""
        report_list = []
        with open(self.log_path, 'r') as f:
            reports = f.read().splitlines()   # Avoid reading with newline character
            for report in reports:
                report_level, report_info = report.split('-')[0], report.split('-')[1]
                item = {
                    'level': report_level,
                    'report_message': report_info
                }
                report_list.append(item)
        self.response_data['reports'] = report_list
        logging.shutdown()

        return {'response': self.response_data}


def open_qgis(request):
    """Open project qgs"""
    os.startfile(QGS_PATH)
    messages.info(request, "S'està obrint el projecte de QGIS")
    return redirect('qa-page')


def render_qa_page(request):
    """
    Render the same qa page itself
    :param request: Rendering of the qa page
    :return:
    """
    return render(request, '../templates/qa_page.html')
