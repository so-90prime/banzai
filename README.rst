BANZAI Pipeline for 90Prime
===========================

This repo contains the data reduction package for the 90Prime imager on Steward Observatory's Bok Telescope.

It is based on Las Cumbres Observatory's BANZAI, which stands for Beautiful Algorithms to Normalize Zillions of Astronomical Images.

See also `<https://banzai.readthedocs.io>`_ for more information.

Please cite the following DOI if you are using processed LCOGT data.

.. image:: https://zenodo.org/badge/26836413.svg
    :target: https://zenodo.org/badge/latestdoi/26836413
    :alt: Zenodo DOI

We have recently implemented a neural network model to detect cosmic rays in ground based images. For more information
pleas see our paper on arXiv. If possible please also cite
`Xu et al., 2021, arXiv:2106.14922 <https://arxiv.org/abs/2106.14922>`_.

Installation
------------
We will use PostgreSQL as our database. Make sure your machine has a PostgreSQL client installed.
We'll also use a Python virtual environment, so make sure that feature is installed.

.. code-block:: bash

    sudo apt install postgresql-client python3-venv

Clone all the code from GitHub into your home directory. Note that our version of BANZAI is on the ``90prime`` branch.
Right now, both the astrometry and photometry repos are private, so I had to add my SSH key to my GitHub account.

.. code-block:: bash

    git clone git@github.com:so-90prime/banzai.git -b 90prime
    git clone git@github.com:griffin-h/photometric-catalog-service.git
    git clone git@github.com:griffin-h/gaia-astrometry.net-service.git

Create the ``.env`` file in the ``banzai`` directory with our database secrets. The format is

.. code-block::

    PGNAME=my_database_name
    PGUSER=my_database_username
    PGPASSWORD=my_database_password
    PGPORT=my_database_port

Set up the Python virtual environment. We're adding the environment variables to the end of the activate script as a shortcut.

.. code-block:: bash

    python3 -m venv ~/banzai_env
    echo '''# custom environment variables for banzai
    set -a
    source $HOME/banzai/.env
    set +a
    export PGHOST=localhost
    export DB_ADDRESS=postgresql://$PGUSER:$PGPASSWORD@$PGHOST:$PGPORT/$PGNAME
    export OPENTSDB_PYTHON_METRICS_TEST_MODE="True"''' >> ~/banzai_env/bin/activate
    source ~/banzai_env/bin/activate
    pip install ~/banzai/

Build and run the Docker containers. The first time you run this, it will take a while to build everything.
(We may need to increase the shared memory of the photometry service the first time in order to ingest the reference catalog.
On the command line this is ``--shm-size=16g`` but I'm not sure how to do it with Docker compose.)

.. code-block:: bash
    
    cd ~/banzai/
    docker compose up -d
    
Initialize the BANZAI database. For 90Prime, we create a different instrument for each chip (90pa, 90pb, 90pc, 90pd).

.. code-block:: bash

    banzai_create_db
    banzai_add_site --site=kpno --longitude=-111.6 --latitude=31.98 --elevation=2120 --timezone=-7
    for chip in a b c d
        do banzai_add_instrument --site=kpno --name=90prime --camera=90p$chip --instrument-type=90prime
    done

Download and ingest the `ATLAS Reference Catalog 2 <https://archive.stsci.edu/hlsp/atlas-refcat2>`_. This will take a really long time (days). We use this for photometric calibration.

.. code-block:: bash

    mkdir /nfs/data/primefocus/atlas-refcat2/
    cd /nfs/data/primefocus/atlas-refcat2/
    wget https://archive.stsci.edu/hlsps/atlas-refcat2/hlsp_atlas-refcat2_atlas_ccd_m33-m15_multi_v1_cat.csv.gz https://archive.stsci.edu/hlsps/atlas-refcat2/hlsp_atlas-refcat2_atlas_ccd_m15-p19_multi_v1_cat.csv.gz https://archive.stsci.edu/hlsps/atlas-refcat2/hlsp_atlas-refcat2_atlas_ccd_p19-p90_multi_v1_cat.csv.gz
    gunzip *.csv.gz
    CATALOG_DB_URL=DB_ADDRESS python ~/photometric-catalog-service/db_creation/create_db.py
    psql -c "CREATE INDEX atlas_refcat2_position_idx ON atlas_refcat2 USING GIST (position);"
    psql -c "vacuum analyze;"

Lastly, copy all the astrometry.net indices to /nfs/data/primefocus/gaia-astrometry.net-indices/. These aren't online at the moment.

BANZAI is installed! If you want to shut down the Docker containers, you can just run

.. code-block:: bash

    cd ~/banzai/
    docker compose down

Usage
-----
Right now we are running BANZAI from the command line. There is no automated process to find and reduce images.

First activate the environment and start the Docker containers.

.. code-block:: bash

    source ~/banzai_env/bin/activate
    cd ~/banzai/
    docker compose up -d

The first time you want to process images, you'll have to manually create the master calibration files.
(We're skipping bad pixel masks for now.) First, the bias frames. You'll notice the main command is ``banzai_reduce_multichip_frame``.

.. code-block:: bash

    for fn in $(ls path/to/raw/data/*.fits)  # fill in this regular expression to get just the bias frames
        do banzai_reduce_multichip_frame --no-bpm --override-missing-calibrations --fpack --processed-path /nfs/data/primefocus/processed --filepath $fn
    done
    for fn in $(ls path/to/reduced/data/*.fits.fz)  # fill in this regular expression to get just the bias frames
        do banzai_mark_frame_as_good --filename $(basename $fn)
    done
    for chip in a b c d
        do banzai_make_master_calibrations --processed-path /nfs/data/primefocus/processed --fpack --no-bpm --site kpno --camera 90p$chip --frame-type bias --min-date 2022-11-02 --max-date 2022-11-03
    done

Then the flat fields. You can include all the filters here and it will sort through them correctly.

.. code-block:: bash

    for fn in $(ls path/to/raw/data/*.fits)  # fill in this regular expression to get just the flat frames
        do banzai_reduce_multichip_frame --no-bpm --override-missing-calibrations --fpack --processed-path /nfs/data/primefocus/processed --filepath $fn
    done
    for fn in $(ls path/to/reduced/data/*.fits.fz)  # fill in this regular expression to get just the flat frames
        do banzai_mark_frame_as_good --filename $(basename $fn)
    done
    for chip in a b c d
        do banzai_make_master_calibrations --processed-path /nfs/data/primefocus/processed --fpack --no-bpm --site kpno --camera 90p$chip --frame-type skyflat --min-date 2022-11-02 --max-date 2022-11-03
    done

Finally, reduce the science frames.

.. code-block:: bash

    for fn in $(ls path/to/raw/data/*.fits)  # fill in this regular expression to get just the science frames
        do banzai_reduce_multichip_frame --no-bpm --override-missing-calibrations --fpack --processed-path /nfs/data/primefocus/processed --filepath $fn
    done

License
-------
The original project is Copyright (c) Las Cumbres Observatory and licensed under the terms of GPLv3. See the LICENSE file for more information.


Support
-------
`Create an issue <https://github.com/so-90prime/banzai/issues>`_

.. image:: http://img.shields.io/badge/powered%20by-AstroPy-orange.svg?style=flat
    :target: http://www.astropy.org
    :alt: Powered by Astropy Badge
