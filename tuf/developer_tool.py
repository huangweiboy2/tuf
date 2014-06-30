"""
<Program Name>
  developer_tool.py

<Authors>
  Santiago Torres <torresariass@gmail.com>
  Zane Fisher <zanefisher@gmail.com>

  Based on the work done for the repository tools by Vladimir Diaz

<Started>
  January 22, 2014 

<Copyright>
  See LICENSE for licensing information.

<Purpose>
  See 'tuf/README-developer-tools.md' for a complete guide on using
  'developer_tool.py'.
"""

# Help with Python 3 compatibility, where the print statement is a function, an
# implicit relative import is invalid, and the '/' operator performs true
# division.  Example:  print 'hello world' raises a 'SyntaxError' exception.
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

#this import is the interface to the whole tuf module, since the imports are 
# made there. 
import os
import errno
import sys
import logging
import shutil
import tempfile
import json

import tuf
import tuf.formats
import tuf.util
import tuf.keydb
import tuf.roledb
import tuf.keys
import tuf.sig
import tuf.log
import tuf.conf
import tuf.repository_tool

from tuf.keys import format_keyval_to_metadata
from tuf.keys import format_metadata_to_key

from tuf.repository_tool import Targets
from tuf.repository_lib import get_metadata_fileinfo
from tuf.repository_lib import get_metadata_filenames
from tuf.repository_tool import generate_and_write_rsa_keypair
from tuf.repository_tool import import_rsa_publickey_from_file
from tuf.repository_tool import import_rsa_privatekey_from_file
from tuf.repository_tool import generate_and_write_ed25519_keypair
from tuf.repository_tool import import_ed25519_publickey_from_file
from tuf.repository_tool import import_ed25519_privatekey_from_file
from tuf.repository_lib import _remove_invalid_and_duplicate_signatures
from tuf.repository_lib import _check_role_keys
from tuf.repository_lib import _delete_obsolete_metadata
from tuf.repository_lib import generate_targets_metadata
from tuf.repository_lib import sign_metadata
from tuf.repository_lib import write_metadata_file
from tuf.repository_lib import _metadata_is_partially_loaded


# See 'log.py' to learn how logging is handled in TUF.
logger = logging.getLogger('tuf.developer_tool')


# Recommended RSA key sizes:
# http://www.emc.com/emc-plus/rsa-labs/historical/twirl-and-rsa-key-size.htm#table1
# According to the document above, revised May 6, 2003, RSA keys of size 3072
# provide security through 2031 and beyond.  2048-bit keys are the recommended
# minimum and are good from the present through 2030.
from tuf.repository_lib import DEFAULT_RSA_KEY_BITS as DEFAULT_RSA_KEY_BITS

# The algorithm used by the developer tools to generate the hashes of the
# target filepaths. 
from tuf.repository_tool import HASH_FUNCTION as HASH_FUNCTION

# The extension of TUF metadata.
from tuf.repository_lib  import METADATA_EXTENSION as METADATA_EXTENSION


# The metadata filename for the targets metadata information.
from tuf.repository_lib import TARGETS_FILENAME as TARGETS_FILENAME

# Project configuration filename. This file is intended to hold all of the
# supporting information about the project that's not contained in a usual
# TUF metadata file. The project.cfg file consists of the following fields:
#
#   targets_location:   the location of the targets folder.
#
#   prefix:             the directory location to prepend to the metadata so it
#                       matches the metadata signed in the repository.
#
#   metadata_location:  the location of the metadata files.
#
#   threshold:          the threshold for this project object, it is fixed to
#                       one in the current version
#   
#   public_keys:        a list of the public keys used to verify the metadata
#                       in this project
#
#   layout_type:        a field describing the directory layout:
#                         
#                         repo-like: matches the layout of the repository tool.
#                                    the targets and metadata folders are 
#                                    located under a comon directory for the
#                                    project.
#
#                         flat:      the targets directory and the
#                                    metadata directory are located in different
#                                    paths
#
#  project_name:       The name of the current project, this value is used to
#                      match the resulting filename with the one in upstream
PROJECT_FILENAME = 'project.cfg'

# The targets and metadata directory names.  Metadata files are written
# to the staged metadata directory instead of the "live" one.
from tuf.repository_tool import METADATA_STAGED_DIRECTORY_NAME
from tuf.repository_tool import METADATA_DIRECTORY_NAME
from tuf.repository_tool import TARGETS_DIRECTORY_NAME

# The full list of supported TUF metadata extensions.
from tuf.repository_lib import METADATA_EXTENSIONS

# The recognized compression extensions. 
from tuf.repository_lib import SUPPORTED_COMPRESSION_EXTENSIONS


# Supported key types.
from tuf.repository_lib import SUPPORTED_KEY_TYPES


class Project(Targets):
  """
  <Purpose>
    This class works as the abstraction of the developer's files. this module
    was created with the objective of simplifying the publishing process using
    TUF by taking care of all of the bookkeeping, signature handling and 
    metadata integrity verification.

    This class is the direct representation of a metadata file* with the 
    intention to provide the ability to modify this data in an OOP manner
    without messing with syntax and sanity-checking.

      
  <Arguments>
    project_name:
      The name of the metadata file as it should be named in the upstream
      repository.

    metadata_directory:
      The metadata sub-directory contains the files of the top-level
      roles, including all roles delegated from 'targets.txt'. 

    targets_directory:
      The targets sub-directory contains all the target files that are
      downloaded by clients and are referenced in TUF Metadata.  The hashes and
      file lengths are listed in Metadata files so that they are securely
      downloaded.  Metadata files are similarly referenced in the top-level
      metadata.

    file_prefix:
      The path strig that will be prepended to the generated metadata
      (e.g. targets/foo -> targets/prefix/foo) so that it matches the actual
      location in the upstream repository.

  <Exceptions>
    tuf.FormatError, if the arguments are improperly formatted.

  <Side Effects>
    Creates top-level role objects and assigns them as attributes.

  <Returns>
    A project object that contains default Metadata objects for the top-level
    roles.
  """
 
  def __init__(self, project_name ,metadata_directory, targets_directory,
      file_prefix):
  
    # Do the arguments have the correct format?
    # Ensure the arguments have the appropriate number of objects and object
    # types, and that all dict keys are properly named.
    # Raise 'tuf.FormatError' if any are improperly formatted.
    tuf.formats.PATH_SCHEMA.check_match(metadata_directory)
    tuf.formats.PATH_SCHEMA.check_match(targets_directory)
    tuf.formats.PATH_SCHEMA.check_match(file_prefix)
    tuf.formats.RELPATH_SCHEMA.check_match(project_name)

    self._metadata_directory = metadata_directory
    self._targets_directory = targets_directory
    self._project_name = project_name

    # layout type defaults to "flat" unless explicitly specified in 
    # create_new_project
    self.layout_type = "flat"

    # Set the top-level role objects, we set the rolename to be the project's
    # name
    super(Project, self).__init__(self._targets_directory, project_name)

    self.prefix = file_prefix




  def write(self, write_partial=False):
    """
    <Purpose>
      Write all the JSON Metadata objects to their corresponding files.
      write() raises an exception if any of the role metadata to be written to
      disk is invalid, such as an insufficient threshold of signatures, missing
      private keys, etc.
    
    <Arguments>
      write_partial:
        A boolean indicating whether partial metadata should be written to
        disk.  Partial metadata may be written to allow multiple maintainters
        to independently sign and update role metadata.  write() raises an
        exception if a metadata role cannot be written due to not having enough
        signatures.

    <Exceptions>
      tuf.Error, if any of the top-level roles do not have a minimum
      threshold of signatures.

    <Side Effects>
      Creates metadata files in the projects's metadata directory.

    <Returns>
      None.
    """
    
    # Does 'write_partial' have the correct format?
    # Ensure the arguments have the appropriate number of objects and object
    # types, and that all dict keys are properly named.
    # Raise 'tuf.FormatError' if any are improperly formatted.
    tuf.formats.BOOLEAN_SCHEMA.check_match(write_partial)
    
    # At this point the tuf.keydb and tuf.roledb stores must be fully
    # populated, otherwise write() throwns a 'tuf.Repository' exception if 
    # any of the top-level roles are missing signatures, keys, etc.

    # Write the metadata files of all the delegated roles.
    delegated_rolenames = \
      tuf.roledb.get_delegated_rolenames(self._project_name)

    for delegated_rolename in delegated_rolenames:
      roleinfo = tuf.roledb.get_roleinfo(delegated_rolename)
      delegated_filename = os.path.join(self._metadata_directory,
                                        delegated_rolename + METADATA_EXTENSION)

      # Ensure the parent directories of 'metadata_filepath' exist, otherwise an
      # IO exception is raised if 'metadata_filepath' is written to a
      # sub-directory.
      tuf.util.ensure_parent_dir(delegated_filename)
      
      _generate_and_write_metadata(delegated_rolename, delegated_filename,
                                   write_partial, self._targets_directory,
                                   self._metadata_directory,
                                   prefix=self.prefix)
      
    
    # Generate the 'project_name' metadata file.
    targets_filename = self._project_name + METADATA_EXTENSION 
    targets_filename = os.path.join(self._metadata_directory, targets_filename)
    project_signable, targets_filename = \
      _generate_and_write_metadata( self._project_name, targets_filename, 
                                   write_partial, self._targets_directory,
                                   self._metadata_directory, prefix=self.prefix)

    #save some other information that is not stored in the project's metadata 
    _save_project_configuration(self._metadata_directory,
        self._targets_directory, self.keys, self.prefix, self.threshold,
        self.layout_type, self._project_name)
    
    # Delete the metadata of roles no longer in 'tuf.roledb'.  Obsolete roles
    # may have been revoked.
    _delete_obsolete_metadata(self._metadata_directory,
                              project_signable['signed'], False)






  def add_verification_key(self,key):
    """
      <Purpose>
        Function as a thin wrapper call for the project._targets call
        with the same name. This wrapper is only for usability purposes

      <Arguments>
        Key:
          The role key to be added, conformant to tuf.formats.anykey_schema
          Adding a public key to a role means that its corresponding private
          key must generate and add its signture to the role. 

      <Exceptions>
        Tuf.FormatError, if the 'key' argument is improperly formatted.

        Tuf.Error, if the project already contains a key

      <Side Effects>
        The role's entries in 'tuf.keydb.py' and 'tuf.roledb.py' are updated

      <Returns>
        None
    """
    ### should check the number of keys for this role.
    if len(self.keys)>0:
      raise tuf.Error("This project already contains a key")

    try:
      super(Project, self).add_verification_key(key)
    except tuf.FormatError:
      raise





  def status(self):
    """
    <Purpose>
      Determine the status of the top-level roles, including those delegated.
      status() checks if each role provides sufficient public keys, signatures,
      and that a valid metadata file is generated if write() were to be called.
      Metadata files are temporary written to check that proper metadata files
      are written, where file hashes and lengths are calculated and referenced
      by the top-level roles.  status() does not do a simple check for number
      of threshold keys and signatures.

    <Arguments>
      None.

    <Exceptions>
      tuf.Error, if any of the top-level roles do not have a minimum
      threshold of signatures.

    <Side Effects>
      Generates and writes temporary metadata files.

    <Returns>
      None.
    """
    temp_project_directory = None

    try:
      temp_project_directory = tempfile.mkdtemp()
      metadata_directory = os.path.join(temp_project_directory,
          self._metadata_directory[1:])

      #targets_directory = os.path.join(temp_project_directory,
      #    self._targets_directory[1:])
      targets_directory = self._targets_directory


      os.makedirs(metadata_directory)
      # os.makedirs(targets_directory)

      #filenames = get_metadata_filenames(metadata_directory)
      # we should do the schema check
      filenames = {}
      filenames['targets'] = os.path.join(metadata_directory,self._project_name)
      
      # Delegated roles.
      delegated_roles = tuf.roledb.get_delegated_rolenames(self._project_name)
      insufficient_keys = []
      insufficient_signatures = []
      
      for delegated_role in delegated_roles:
        try: 
          _check_role_keys(delegated_role)
        except tuf.InsufficientKeysError:
          insufficient_keys.append(delegated_role)
          continue
        
        roleinfo = tuf.roledb.get_roleinfo(delegated_role)
        try: 
          signable =  _generate_and_write_metadata(delegated_role,
                                                filenames['targets'], False,
                                                targets_directory,
                                                metadata_directory,
                                                False)
          self._print_status(delegated_role, signable[0])
        except tuf.Error:
          insufficient_signatures.append(delegated_role)
      
      if len(insufficient_keys):
        message = 'Delegated roles with insufficient keys: '+ \
          repr(insufficient_keys)
        print(message)
        return

      if len(insufficient_signatures):
        message = 'Delegated roles with insufficient signatures: '+ \
          repr(insufficient_signatures)
        print(message) 
        return

      # Targets role.
      try: 
        _check_role_keys(self.rolename)
      except tuf.InsufficientKeysError as e:
        print(str(e))
        return
      
      try:
        signable, filename =  _generate_and_write_metadata(self._project_name,
                                                filenames['targets'], False,
                                                targets_directory,
                                                metadata_directory,
                                                False)
        self._print_status(self._project_name, signable)
      except tuf.Error as e:
        signable = e[1]
        self._print_status(self._project_name, signable)
        return

    finally:
      shutil.rmtree(temp_project_directory, ignore_errors=True)





  def _print_status(self, rolename, signable):
    """
    Non-public function prints the number of (good/threshold) signatures of
    'rolename'.
    """

    status = tuf.sig.get_signature_status(signable, rolename)
    
    message = repr(rolename)+' role contains '+ \
      repr(len(status['good_sigs']))+' / '+ \
      repr(status['threshold'])+' signatures.'
    print(message)





def _generate_and_write_metadata(rolename, metadata_filename, write_partial,
                                 targets_directory, metadata_directory,
                                 filenames=None,
                                 prefix=''):
  """
    Non-public function that can generate and write the metadata of the
    specified
    top-level 'rolename'.  It also increments version numbers if:
    
    1.  write_partial==True and the metadata is the first to be written.
              
    2.  write_partial=False (i.e., write()), the metadata was not loaded as
        partially written, and a write_partial is not needed.
  """

  metadata = None 
  # Retrieve the roleinfo of 'rolename' to extract the needed metadata
  # attributes, such as version number, expiration, etc.
  roleinfo = tuf.roledb.get_roleinfo(rolename) 

  metadata = generate_targets_metadata(targets_directory,
                                       roleinfo['paths'],
                                       roleinfo['version'],
                                       roleinfo['expires'],
                                       roleinfo['delegations'],
                                       False) 

  # preprend the prefix to the project's filepath to avoid signature errors
  # in upstream
  target_filepaths = metadata['targets'].items()
  for element in list(metadata['targets']):
    junk_path, relative_target = os.path.split(element)
    prefixed_path = os.path.join(prefix,relative_target)
    metadata['targets'][prefixed_path] = metadata['targets'][element]
    if prefix != '':
      del(metadata['targets'][element])

  signable = sign_metadata(metadata, roleinfo['signing_keyids'],
                           metadata_filename)

  # Check if the version number of 'rolename' may be automatically incremented,
  # depending on whether if partial metadata is loaded or if the metadata is
  # written with write() / write_partial(). 
  # Increment the version number if this is the first partial write.
  if write_partial:
    temp_signable = sign_metadata(metadata, [], metadata_filename)
    temp_signable['signatures'].extend(roleinfo['signatures'])
    status = tuf.sig.get_signature_status(temp_signable, rolename)
    if len(status['good_sigs']) == 0:
      metadata['version'] = metadata['version'] + 1
      signable = sign_metadata(metadata, roleinfo['signing_keyids'],
                               metadata_filename)
  
  # non-partial write()
  else:
    if tuf.sig.verify(signable, rolename): #and not roleinfo['partial_loaded']:
      metadata['version'] = metadata['version'] + 1
      signable = sign_metadata(metadata, roleinfo['signing_keyids'],
                               metadata_filename)

  # Write the metadata to file if contains a threshold of signatures. 
  signable['signatures'].extend(roleinfo['signatures']) 
  
  if tuf.sig.verify(signable, rolename) or write_partial:
    _remove_invalid_and_duplicate_signatures(signable)
    compressions = roleinfo['compressions']
    filename = write_metadata_file(signable, metadata_filename, compressions,
                                   False)
    
    
  # 'signable' contains an invalid threshold of signatures. 
  else:
    message = 'Not enough signatures for '+repr(metadata_filename)
    raise tuf.Error(message, signable)


  # The root and timestamp files should also be written without a digest if
  # 'consistent_snaptshots' is True.  Client may request a timestamp and root
  # file without knowing its digest and file size.
  return signable, filename 




def create_new_project(project_name, metadata_directory, location_in_repository = '',
      targets_directory=None, key=None):
  """
  <Purpose>
    Create a new project object, instantiate barebones metadata for the 
    targets, and return a blank project object.  On disk, create_new_project()
    only creates the directories needed to hold the metadata and targets files.
    The project object returned can be directly modified to meet the designer's
    criteria and then written using the method project.write().

    The project name provided is the one that will be added to the resulting
    metadata file as it should be named in upstream.

  <Arguments>
    project_name:
      The name of the project as it should be called in upstream. For example
      targets/unclaimed/django should have its project_name set to "django"

    metadata_directory:
      The directory that will eventually hold the metadata and target files of
      the project.

    targets_directory:
      An optional argument to point the targets directory somewhere else than
      the metadata directory if, for example, a project structure already
      exists and the user does not want to move it.

    location_in_repository:
      An optional argument to hold the "prefix" or the expected location for 
      the project files in the "upstream" respository. This value is only
      used to sign metadata in a way that it matches the future location
      of the files.

      For example, targets/unclaimed/django should have its project name set to
      "targets/unclaimed"

    key:
      The public key to verify the project's metadata. Projects can only
      handle one key with a threshold of one. If a project were to modify it's
      key it should be removed and updated. 
  
  <Exceptions>
    tuf.FormatError, if the arguments are improperly formatted or if the public
      key is not a valid one ( if it's not none ).

    OSError, if the filepaths provided do not have write permissions

  <Side Effects>
    The 'metadata_directory' and 'targets_directory'  directories are created
    if they do not exist.
    
  <Returns>
    A 'tuf.developer_tool.Project' object.
  """

  # Does 'metadata_directory' have the correct format?
  # Ensure the arguments have the appropriate number of objects and object
  # types, and that all dict keys are properly named.
  # Raise 'tuf.FormatError' if there is a mismatch.
  tuf.formats.PATH_SCHEMA.check_match(metadata_directory)
 
  # Do the same for the location in the repo and the project name, we must
  # ensure they are valid pathnames.
  tuf.formats.PATH_SCHEMA.check_match(location_in_repository)
  tuf.formats.RELPATH_SCHEMA.check_match(project_name)

  # for the targets directory we do the same, but first, let's find out what
  # layout the user needs, layout_type is a variable that is usually set to
  # 1, which means "flat" (i.e. the cfg file is where the metadata folder is 
  # located), with a two, the cfg file goes to the "metadata" folder, and a 
  # new metadata folder is created inside the tree, to separate targets and
  # metadata. 
  layout_type="flat"
  if targets_directory is None:
    targets_directory = os.path.join(metadata_directory,TARGETS_DIRECTORY_NAME)
    metadata_directory = \
        os.path.join(metadata_directory,METADATA_DIRECTORY_NAME)
    layout_type="repo-like"
    

  tuf.formats.PATH_SCHEMA.check_match(targets_directory);

  if key is not None:
    tuf.formats.KEY_SCHEMA.check_match(key)

  # Set the metadata and targets directories.  These directories
  # are created if they do not exist.
  metadata_directory = os.path.abspath(metadata_directory)
  targets_directory = os.path.abspath(targets_directory)
  
  # Try to create the metadata directory that will hold all of the metadata
  # files, such as 'root.txt' and 'release.txt'.
  try:
    message = 'Creating '+repr(metadata_directory)
    logger.info(message)
    os.makedirs(metadata_directory)
  
  # 'OSError' raised if the leaf directory already exists or cannot be created.
  # Check for case where 'repository_directory' has already been created. 
  except OSError as e:
    if e.errno == errno.EEXIST:
      # should check if we have write permissions here
      pass 
    else:
      raise

  # Try to create the targets directory that will hold all of the target files.
  try:
    message = 'Creating '+repr(targets_directory)
    logger.info(message)
    os.mkdir(targets_directory)
  except OSError as e:
    if e.errno == errno.EEXIST:
      pass
    else:
      raise
 
  # Create the bare bones project object, where only the top-level roles
  # have been set and contain default values (e.g., Root roles has a threshold
  # of 1, expires 1 year into the future, etc.)
  project = Project(project_name, metadata_directory, targets_directory,
      location_in_repository)

  # add the key to the project. 
  if key is not None:
    project.add_verification_key(key);
  
  # save the layout information
  project.layout_type = layout_type

  return project






def _save_project_configuration(metadata_directory,targets_directory,
                                public_keys, prefix, threshold, layout_type, 
                                project_name):
  """
  <Purpose>
    Persist the project's information in a file to provide the information
    for the load routine

  <Arguments>
    metadata_directory:
      Where the project's metadata is located

    targets_directory:
      The location of the target files in this project.
    
    public_keys:
      A list containing the public keys for the project role.
    
    prefix: 
      The project's prefix (if any)
    
    threshold: 
      The threshold value for the project role.
  
    layout_type:
      The layout type being used by the project, "flat" stands for separated
      targets and metadata directories, "repo-like" emulates the layout used
      by the repository tools

    project_name:
      The name given to the project, this sets the metadata filename so it
      matches the one stored in upstream.

  <Exceptions>
    OSError may rise if the metadata_directory/project.cfg file exists and
    is non-writeable

    tuf.FormatError are also expected if any of the arguments are malformed.

  <Side Effects>
    A project.cfg file is created or overwritten

  <Returns>
    nothing
  """

  # schema check for metadata_directory and prefix
  tuf.formats.PATH_SCHEMA.check_match(metadata_directory)
  tuf.formats.PATH_SCHEMA.check_match(prefix)
  tuf.formats.PATH_SCHEMA.check_match(targets_directory)
  tuf.formats.RELPATH_SCHEMA.check_match(project_name)

  # get the absolute filepath to our metadata_directory for consistency
  #metadata_directory = os.path.abspath(metadata_directory)
  cfg_file_directory = metadata_directory

  # check wheter if the layout type is "flat" or "repo-like" 
  # if it is, the .cfg file should be saved in the previous directory.
  if(layout_type == "repo-like"):
    cfg_file_directory = os.path.dirname(metadata_directory)
    absolute_location, targets_directory = os.path.split(targets_directory)

  absolute_location, metadata_directory = os.path.split(metadata_directory)
  # is the file open-able? open for overwriting
  project_filename = os.path.join(cfg_file_directory,PROJECT_FILENAME)
  
  # build the data structure
  project_config = {}
  project_config['prefix'] = prefix
  project_config['public_keys'] = {}
  project_config['metadata_location'] = metadata_directory
  project_config['targets_location'] = targets_directory
  project_config['threshold'] = threshold
  project_config['layout_type'] = layout_type
  project_config['project_name'] = project_name

  # build a dictionary containing the actual keys
  for key in public_keys:
    key_info = tuf.keydb.get_key(key)
    key_metadata = format_keyval_to_metadata(key_info['keytype'],
        key_info['keyval'])
    project_config['public_keys'][key] = key_metadata

  # save the actual data
  with open(project_filename,"wt") as fp:
    json.dump(project_config,fp)






def load_project(project_directory, prefix='', new_targets_location=None):
  """
  <Purpose>
    Return a project object initialized with the contents of the metadata 
    files loaded from the project_directory path

  <Arguments>
    project_directory: 
      The path to the project's folder

    prefix:
      the prefix for the metadata, if defined, it will replace the current
      prefix, by first removing the existing one (Saved) and setting the new
      one in the end. 

    new_targets_location:
      For flat project configurations, you might want to reload the project 
      with a new location for the target files. This overwrites the previous
      path to search for the target files.

  <Exceptions>
    tuf.FormatError, if 'project_directory' or any of the metadata files
    are improperly formatted. 

  <Side Effects>
   All the metadata files found in the project are loaded and their contents
   stored in a libtuf.Repository object.

  <Returns>
    A  tuf.developer_tool.Project object.
  
  """
  # Does 'repository_directory' have the correct format?
  # Raise 'tuf.FormatError' if there is a mismatch.
  tuf.formats.PATH_SCHEMA.check_match(project_directory)
  # do the same for the prefix
  tuf.formats.PATH_SCHEMA.check_match(prefix)

  # clear the role and keydbs
  tuf.roledb.clear_roledb() 
  tuf.keydb.clear_keydb()

  # Locate metadata filepaths and targets filepath.
  project_directory = os.path.abspath(project_directory)
  
  
  # load the cfg file and the project.
  config_filename = os.path.join(project_directory,PROJECT_FILENAME)
  try:
    project_configuration = tuf.util.load_json_file(config_filename)
    tuf.formats.PROJECT_CFG_SCHEMA.check_match(project_configuration) 
  except (OSError, IOError) as e:
    raise
 
  targets_directory = os.path.join(project_directory,
      project_configuration['targets_location'])
  
  if project_configuration['layout_type'] == 'flat':
    project_directory, relative_junk = os.path.split(project_directory)
    targets_directory = project_configuration['targets_location']
    if new_targets_location is not None:
      targets_directory = new_targets_location

  metadata_directory = os.path.join(project_directory,
      project_configuration['metadata_location'])

  new_prefix = None
  if prefix != '':
    new_prefix = prefix
    
  prefix = project_configuration['prefix']
 
  # load the projects filename.
  project_name = project_configuration['project_name']
  project_filename = project_name + METADATA_EXTENSION

  # create a blank project on the target directory
  project = Project(project_name, metadata_directory, targets_directory, prefix)

  project.threshold = project_configuration['threshold']
  project.prefix = project_configuration['prefix']
  project.layout_type = project_configuration['layout_type']

  # traverse the public keys and add them to the project
  keydict = project_configuration['public_keys']
  for keyid in keydict:
    key = format_metadata_to_key(keydict[keyid]) 
    project.add_verification_key(key)
 
  # load the project's metadata
  targets_metadata_path = os.path.join(project_directory, metadata_directory,
      project_filename)
  signable = tuf.util.load_json_file(targets_metadata_path)
  tuf.formats.check_signable_object_format(signable)
  targets_metadata = signable['signed']
  
  # remove the prefix from the metadata
  targets_metadata = _strip_prefix_from_targets_metadata(targets_metadata,
                                            prefix) 
  for signature in signable['signatures']:
    project.add_signature(signature)

  # update roledb
  roleinfo = tuf.roledb.get_roleinfo(project_name)
  roleinfo['signatures'].extend(signable['signatures'])
  roleinfo['version'] = targets_metadata['version']
  roleinfo['paths'] = list(targets_metadata['targets'])
  roleinfo['delegations'] = targets_metadata['delegations']
  roleinfo['partial_loaded'] = False
  

  # check if the loaded metadata was partially written and update the 
  # flag in the roledb
  if _metadata_is_partially_loaded(project_name, signable, roleinfo):
    roleinfo['partial_loaded'] = True

  tuf.roledb.update_roleinfo(project_name, roleinfo)

  
  
  for key_metadata in targets_metadata['delegations']['keys'].values():
    key_object = tuf.keys.format_metadata_to_key(key_metadata)
    tuf.keydb.add_key(key_object)

  for role in targets_metadata['delegations']['roles']:
    rolename = role['name']
    roleinfo = {'name': role['name'], 'keyids': role['keyids'],
                'threshold': role['threshold'], 'compressions': [''],
                'signing_keyids': [], 'signatures': [], 'partial_loaded':False,
                'delegations': {'keys':{}, 'roles':[]}
                }
    tuf.roledb.add_role(rolename, roleinfo)
                                                        
  # Load delegated targets metadata.
  # Walk the 'targets/' directory and generate the fileinfo of all the files
  # listed.  This information is stored in the 'meta' field of the release
  # metadata object.
  targets_objects = {}
  loaded_metadata = []
  targets_objects[project_name] = project
  metadata_directory = os.path.join(project_directory, metadata_directory)
  targets_metadata_directory = os.path.join(metadata_directory, project_name)
  if os.path.exists(targets_metadata_directory) and \
                    os.path.isdir(targets_metadata_directory):
    for root, directories, files in os.walk(targets_metadata_directory):
      
      # 'files' here is a list of target file names.
      for basename in files:
        metadata_path = os.path.join(root, basename)
        metadata_name = \
          metadata_path[len(metadata_directory):].lstrip(os.path.sep)

        # strip the extension
        if metadata_name.endswith(METADATA_EXTENSION): 
          extension_length = len(METADATA_EXTENSION)
          metadata_name = metadata_name[:-extension_length]
        else:
          continue

        signable = None
        try:
          signable = tuf.util.load_json_file(metadata_path)
        except (ValueError, IOError, tuf.Error):
          raise
        
        # strip the prefix from the local working copy, it will be added again
        # at the time of writing.
        metadata_object = signable['signed']
        metadata_object = _strip_prefix_from_targets_metadata(metadata_object,
                                             prefix) 
  
        roleinfo = tuf.roledb.get_roleinfo(metadata_name)
        roleinfo['signatures'].extend(signable['signatures'])
        roleinfo['version'] = metadata_object['version']
        roleinfo['expires'] = metadata_object['expires']
        roleinfo['paths'] = list(metadata_object['targets'])
        roleinfo['delegations'] = metadata_object['delegations']
        roleinfo['partial_loaded'] = False
      
        if os.path.exists(metadata_path+'.gz'):
          roleinfo['compressions'].append('gz')

        # if the metadata was partially loaded, update the roleinfo flag.
        if _metadata_is_partially_loaded(metadata_name, signable, roleinfo):
          roleinfo['partial_loaded'] = True


        tuf.roledb.update_roleinfo(metadata_name, roleinfo)

        # append to list of elements to avoid reloading repeated metadata
        loaded_metadata.append(metadata_name)

        # add the delegation
        new_targets_object = Targets(targets_directory, metadata_name, roleinfo)
        targets_object = \
          targets_objects[tuf.roledb.get_parent_rolename(metadata_name)]
        targets_objects[metadata_name] = new_targets_object
        
        targets_object._delegated_roles[(os.path.basename(metadata_name))] = \
                              new_targets_object

        # Add the keys specified in the delegations field of the Targets role.
        for key_metadata in metadata_object['delegations']['keys'].values():
          key_object = tuf.keys.format_metadata_to_key(key_metadata)
          try: 
            tuf.keydb.add_key(key_object)
          except tuf.KeyAlreadyExistsError:
            pass
        
        for role in metadata_object['delegations']['roles']:
          rolename = role['name'] 
          roleinfo = {'name': role['name'], 'keyids': role['keyids'],
                      'threshold': role['threshold'],
                      'compressions': [''], 'signing_keyids': [],
                      'signatures': [],
                      'partial_loaded': False,
                      'delegations': {'keys': {},
                                      'roles': []}}
          tuf.roledb.add_role(rolename, roleinfo)

  if new_prefix:
    project.prefix = new_prefix
  return project





def _strip_prefix_from_targets_metadata(targets_metadata, prefix):
  """ non-public method that removes the prefix from the targets metadata
      so it can be used again in compliance with the local copies
  """
  unprefixed_targets_metadata = {}
  for targets in targets_metadata['targets'].keys():
    unprefixed_target = os.path.relpath(targets, prefix)
    unprefixed_target = '/' + unprefixed_target
    unprefixed_targets_metadata[unprefixed_target] = \
                            targets_metadata['targets'][targets] 
  targets_metadata['targets'] = unprefixed_targets_metadata
  
  return targets_metadata 





if __name__ == '__main__':
  # The interactive sessions of the documentation strings can
  # be tested by running libtuf.py as a standalone module:
  # $ python libtuf.py.
  import doctest
  doctest.testmod()
