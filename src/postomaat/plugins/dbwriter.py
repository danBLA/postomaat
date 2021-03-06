# -*- coding: UTF-8 -*-
#   Copyright 2012-2018 Oli Schacher
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
#
from postomaat.shared import ScannerPlugin, strip_address, extract_domain
from postomaat.extensions.sql import SQL_EXTENSION_ENABLED,get_session


class DBWriter(ScannerPlugin):
    
    def __init__(self,config,section=None):
        ScannerPlugin.__init__(self,config,section)
        self.logger=self._logger()
        self.requiredvars={
            'dbconnection':{
                'default':"mysql://root@localhost/dbwriter?charset=utf8",
                'description':'SQLAlchemy Connection string',
            },
             
            'table':{
                'default': "maillog",
                'description': """Tablename where we should insert mails"""

            }, 
                           
            'fields':{
                'default': "from_address to_address from_domain to_domain size queue_id:queueid",
                'description': """Fields that should be inserted. use <fieldname>:<columnname> or just <fieldname> if the database column name matches the fieldname"""
            },              
                             
        }
        
    def get_fieldmap(self):
        """create the mapping from tags to column names based on the config string
        by default, column name is the same as tag name, but the config field can be in the form
        tagname:columname to override the mapping.
        
        eg.
        fields=to_address to_domain from_address:sender from_domain:senderdomain size queueid:postfixqueue
        
        the fieldmap contains the Database Column Name 
        """
        configstring=self.config.get(self.section,'fields')
        fields=configstring.split()
        
        fieldmap={}
        for field in fields:
            if ':' in field:
                (tag,column)=field.split(':',1)
                fieldmap[column]=tag
            else:
                fieldmap[field]=field
        return fieldmap
    
    def lint(self):
        if not SQL_EXTENSION_ENABLED:
            print("sqlalchemy is not installed")
            return False
        
        
        #check fieldmap, select all fields (if we can't select, we can't insert)
        if not self.checkConfig():
            return False
        
        tablename=self.config.get(self.section,'table')
        fieldmap=self.get_fieldmap()
        requiredcolumnnames=fieldmap.keys()
        dbcolumns=",".join(requiredcolumnnames)
        try:
            conn=get_session(self.config.get(self.section,'dbconnection'))
        except Exception as e:
            print("DB Connection failed. Reason: %s"%(str(e)))
            return False
        
        sql_query="SELECT %s FROM %s LIMIT 0,1"%(dbcolumns,tablename)
        try:
            conn.execute(sql_query)
        except Exception as e:
            print("Table or field configuration error: %s"%str(e))
            return False
        return True
              

    def examine(self,suspect):
        try:
            tablename=self.config.get(self.section,'table')
            
            sender=suspect.get_value('sender')
            if sender is not None:
                from_address=strip_address(sender)
                from_domain=extract_domain(from_address)
            else:
                from_address=None
                from_domain=None
          
            recipient=suspect.get_value('recipient')
            if recipient is not None:
                to_address=strip_address(recipient)
                to_domain=extract_domain(to_address)
            else:
                to_address=None
                to_domain=None
            
            fields=suspect.values.copy()
            fields['from_address']=from_address
            fields['from_domain']=from_domain
            fields['to_address']=to_address
            fields['to_domain']=to_domain
            fields['timestamp']=suspect.timestamp
            
            #build query
            fieldmap=self.get_fieldmap()
            requiredcolumnnames=fieldmap.keys()
            dbcolumns=",".join(requiredcolumnnames)
            placeholders=",".join(map(lambda x:u':'+x, requiredcolumnnames))
            sql_insert="INSERT INTO %s (%s) VALUES (%s)"%(tablename,dbcolumns,placeholders)
            
            #
            
            #fill the required vars into new dict with the db columns
            data={}
            for col in requiredcolumnnames:
                postfixfieldname=fieldmap[col]
                if postfixfieldname in fields:
                    #a fiew fields are numeric.. convert them
                    if postfixfieldname in ['recipient_count','size','encryption_keysize']:
                        data[col]=int(fields[postfixfieldname])
                    else:
                        data[col]=fields[postfixfieldname]
                else:
                    data[col]=None
            
            #print sql_insert
            #print data
            conn=get_session(self.config.get(self.section,'dbconnection'))
            conn.execute(sql_insert,data)
        except Exception as e:
            self.logger.error("DB Writer plugin failed, Log not written. : %s"%str(e))
            
        return DUNNO,None

    def __str__(self):
        return "Database Log Plugin"