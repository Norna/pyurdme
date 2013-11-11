from model import *

import numpy as np
import scipy.sparse as scisp
import scipy.io as spio
import subprocess
import os
import tempfile
import re
import sys
import shutil

import gmsh
import numpy
import scipy.sparse

try:
    import h5py
except:
    print "pyurdme requires h5py."
    raise

try:
    import dolfin
    dolfin.parameters["linear_algebra_backend"] = "uBLAS"
except:
    print "Warning: Could not import dolphin. Only simple Cartesain examples will work."
    ONLY_CARTESIAN=True

class MeshImportError(Exception):
    pass


class URDMEModel(Model):
    """ 
        An URDME Model extends Model with spatial information and methods to create URDME solver input.
        TODO: Documentiation.
    """
    
    def __init__(self,name=""):
        Model.__init__(self,name)
        
        # urdme_solver_data will hold all the datastructures needed by the URDME
        # core solvers after the model is initialized.
        self.urdme_solver_data = {'initialized':False}
        self.tspan = None
        self.mesh = None
    
    def __initializeSpeciesMap(self):
        i=0
        self.species_map = {}
        for S in self.listOfSpecies:
            self.species_map[S]=i
            i = i+1;

    def speciesMap(self):
        """ Get the species map, name to index. """
        if not hasattr(self,'species_map'):
            self.__initializeSpeciesMap()
        
        return self.species_map

    def createStoichiometricMatrix(self):
        """ Generate a stoichiometric matrix in sparse CSC format. """
        
        if self.getNumReactions() > 0:
            ND = np.zeros((self.getNumSpecies(),self.getNumReactions()))
            for i,r in enumerate(self.listOfReactions):
                
                R = self.listOfReactions[r]
                reactants = R.reactants
                products  = R.products
                
                for s in reactants:
                    ND[self.species_map[s],i]-=reactants[s]
                for s in products:
                    ND[self.species_map[s],i]+=products[s]
    
            N = scisp.csc_matrix(ND)
        else:
            N = numpy.zeros((self.getNumSpecies(),self.getNumReactions()))

        return N

    def createDependencyGraph(self):
        """ Construct the sparse dependecy graph. """
        
        #TODO: Automatically create a dependency graph (cannot be optimal, but good enough.)
        GF = np.ones((self.getNumReactions(),self.getNumReactions()+self.getNumSpecies()))
        try:
            G=scisp.csc_matrix(GF)
        except:
            G=GF

        return G
    
    def createNewPropensityFile(self,file_name=None):
        """ Generate a C propensity file on the new experimental format. """
        
        template = open(os.path.abspath(os.path.dirname(__file__))+'/data/propensity_file_new_template.c','r')
        propfile = open(file_name,"w")
        propfilestr = template.read()

        propfilestr = propfilestr.replace("__NUMBER_OF_REACTIONS__",str(self.getNumReactions()))
        propfilestr = propfilestr.replace("__NUMBER_OF_SPECIES__",str(len(self.listOfSpecies)))

        
        speciesdef = ""
        for i,sname in enumerate(self.listOfSpecies):
            S = self.listOfSpecies[sname]
            speciesdef += "species *"+sname+";\n\t"
            speciesdef += sname+"= (species *)malloc(sizeof(species));\n\t"
            speciesdef += sname+"->gamma = "+str(S.diffusion_constant)+";\n\t"
            speciesdef += sname+"->sigma = "+str(S.reaction_radius)+";\n\t"
            speciesdef += "ptr["+str(i)+"] = "+sname +";\n\n\t"
            
                                
        propfilestr = propfilestr.replace("__DEFINE_SPECIES__",speciesdef)
        
        
        # Make sure all paramters are evaluated to scalars before we write them to the file.
        self.resolveParameters()
                
        reacstr = ""
                
        for j,sname in enumerate(self.listOfSpecies):
            reacstr += "int "+sname+"="+str(j)+";\n\t"
        
        reacstr += "\n\t"
                
        for i,rname in enumerate(self.listOfReactions):
            R=self.listOfReactions[rname]
            
            reacstr += "reaction *"+rname+";\n\t"
            reacstr += rname+"=(reaction *)malloc(sizeof(reaction));\n\t"
            reacstr += rname+"->order="+str(len(R.reactants))+";\n\t"
            reacstr += rname+"->nr_reactants="+str(len(R.reactants))+";\n\t"
            reacstr += rname+"->nr_products="+str(len(R.products))+";\n\t"
            
            #print reacstr
            
            reacstr += rname+"->reactants=(int *)malloc("+rname+"->nr_reactants*sizeof(int));\n\t"
            for j,reactant in enumerate(R.reactants):
                reacstr += rname+"->reactants["+str(j)+"]="+str(reactant)+";\n\t"
            
            reacstr += "\n\t"+rname+"->products=(int *)malloc("+rname+"->nr_products*sizeof(int));\n\t"
            for j,product in enumerate(R.products):
                reacstr += rname+"->products["+str(j)+"]="+str(product)+";\n\t"
    
            reacstr += "\n\t"+rname+"->nr=(int *)calloc("+str(len(self.listOfSpecies))+",sizeof(int));\n\t"
                
            for j,reactant in enumerate(R.reactants):
                 reacstr += rname+"->nr["+reactant+"]=-"+str(R.reactants[reactant])+";\n\t"

            for j,product in enumerate(R.products):
                reacstr += rname+"->nr["+product+"]="+str(R.products[product])+";\n\t"
                
            reacstr += rname+"->k="+str(R.marate.value)+";\n\t"

            reacstr += "\n\tptr["+str(i)+"] = "+rname +";\n\n\t"
                
                
        propfilestr = propfilestr.replace("__DEFINE_REACTIONS__",reacstr)
        
        propfile.write(propfilestr)
        propfile.close()

    
    def createPropensityFile(self,file_name=None):
        """ Generate the C propensity file that is used to compile the URDME solvers.
            Only mass action propensities are supported. """
        
        
        template = open(os.path.abspath(os.path.dirname(__file__))+'/data/propensity_file_template.c','r')
        propfile = open(file_name,"w")
        propfilestr = template.read()
        
        speciesdef = ""
        i=0
        for S in self.listOfSpecies:
            speciesdef += "#define "+S+" " +"x["+str(i)+"]"+"\n"
            i+=1
        
        propfilestr = propfilestr.replace("__DEFINE_SPECIES__",speciesdef)
    
        propfilestr = propfilestr.replace("__NUMBER_OF_REACTIONS__",str(self.getNumReactions()))
        
        # Make sure all paramters are evaluated to scalars before we write them to the file.
        self.resolveParameters()
        parameters = ""
        for p in self.listOfParameters:
            parameters += "const double "+p+" = " +str(self.listOfParameters[p].value)+";\n"
        propfilestr=propfilestr.replace("__DEFINE_PARAMETERS__",str(parameters))
    
        # Reactions
        funheader = "double __NAME__(const int *x, double t, const double vol, const double *data, int sd)"
    
        funcs = ""
        funcinits = ""
        i = 0
        for R in self.listOfReactions:
            func = ""
            rname=self.listOfReactions[R].name
            func += funheader.replace("__NAME__",rname) + "\n{\n"
            if self.listOfReactions[R].restrict_to == None:
                func += "    return " + self.listOfReactions[R].propensity_function
                order = len(self.listOfReactions[R].reactants)
                if order == 2:
                    func += "/vol;"
                elif order == 0:
                    func += "*vol;"
                else:
                    func += ";"

            else:
                func += "if("
                for sd in self.listOfReactions[R].restrict_to:
                    func += "sd == "+str(sd)+"||"
                func = func[:-2]
                func += ")\n"
                func += "\treturn " + self.listOfReactions[R].propensity_function
                order = len(self.listOfReactions[R].reactants)
                if order == 2:
                    func += "/vol;"
                elif order == 0:
                    func += "*vol;"
                else:
                    func += ";"

                func += "\nelse"
                func += "\n\treturn 0.0;"
        
            
            func +="\n}"
            funcs += func + "\n\n"
            funcinits += "    ptr["+str(i)+"] = " + rname +";\n"
            i+=1
              
        propfilestr = propfilestr.replace("__DEFINE_REACTIONS__",funcs)
        propfilestr = propfilestr.replace("__DEFINE_PROPFUNS__",funcinits)
                
        propfile.write(propfilestr)
        propfile.close()
    
    def timespan(self, tspan):
        """ Set the time span of simulation. """
        self.tspan = tspan
    
    def initializeSubdomainVector(self):
        """ Create the 'sd' vector. """
        # TODO: Support arbitrary sd-numbers and more than one subdomain
        return np.ones((1,self.mesh.getNumVoxels()))
    
    def initializeInitialValue(self):
        """ Create all-zeros inital condition matrix. """
        ns = self.getNumSpecies()
        nv = self.mesh.getNumVoxels()
        self.u0 = np.zeros((ns,nv))
    
    def meshextend(self):
        """ Extend the primary mesh with information about the degrees of freedom.
            
            TODO: Docs...
            
            """
        
        xmesh = Xmesh()
        
        # Construct a species map (dict mapping model species name to an integer index)
        species_map=self.speciesMap()
        
        # Initialize the function spaces and dof maps.
        for spec in self.listOfSpecies:
            
            species = self.listOfSpecies[spec]
            spec_name = species.name
            spec_index = species_map[spec_name]
            
            xmesh.function_space[spec_name] = dolfin.FunctionSpace(self.mesh.mesh,"Lagrange",1)
            # vertex_to_dof_map provides a map between the vertex index and the dof.
            xmesh.vertex_to_dof_map[spec_name]=xmesh.function_space[spec_name].dofmap().dof_to_vertex_map(self.mesh.mesh)
            xmesh.vertex_to_dof_map[spec_name]=len(self.listOfSpecies)*xmesh.vertex_to_dof_map[spec_name]+spec_index
            xmesh.vertex_to_dof_map[spec_name]=xmesh.vertex_to_dof_map[spec_name]
        
        
        xmesh.vertex = self.mesh.mesh.coordinates()
        self.xmesh = xmesh
        
    
    # Some utility routines to set initial conditions follow
    def scatter(self,species,subdomain=None):
        """ Scatter an initial number of molecules over the voxels in a subdomain. """
    
        spec_name = species.name
        num_spec = species.initial_value
        species_map = self.speciesMap()
        specindx= species_map[spec_name]
        
        if not hasattr(self,"u0"):
            self.initializeInitialValue()
        
        if not hasattr(self,'xmesh'):
            self.meshextend()
        
        # Map vertex index to dofs
        dofind = self.xmesh.vertex_to_dof_map[spec_name]

        active_on = species.active_on
        if active_on is not None:
            sd = self.sd
            table = []
            for i,ind in enumerate(sd):
                if ind in active_on:
                   table.append(i)
        else:
            table = range(self.mesh.getNumVoxels())
            
        ltab = len(table)

        for mol in range(species.initial_value):
            vtx=np.random.randint(0,ltab)
            ind = table[vtx]
            dof = dofind[ind]
            ix = (dof-specindx)/len(species_map)
            self.u0[specindx,ix]+=1

    def placeNear(self,species=None, point=None):
        """ Place all molecules of kind species in the voxel nearest a given point. """
    
        spec_name = species.name
        num_spec = species.initial_value
    
        if not hasattr(self,"u0"):
            self.initializeInitialValue()
        
        if not hasattr(self,'xmesh'):
            self.meshextend()

        # Find the voxel with center (vertex) nearest to the point
        coords = self.mesh.getVoxels()
        shape = coords.shape
                
        p = dolfin.Point(point[0],point[1])
                
        reppoint = numpy.tile(point,(shape[0],1))
        dist = numpy.sqrt(numpy.sum((coords-reppoint)**2,axis=1))
        ix = numpy.argmin(dist)
       
        species_map = self.speciesMap()
        specindx = species_map[spec_name]
        dofind = self.xmesh.vertex_to_dof_map[spec_name][ix]
        ix = (dofind-specindx)/len(species_map)
        self.u0[specindx,ix]=num_spec

    
    def createSystemMatrix(self):
        """ 
            Create the system (diffusion) matrix for input to the URDME solvers. The matrix
            is built by concatenating the individually assembled matrices for each of the species,
            and multiplying with the lumped mass matrix (which define the volume of the voxels).
            Negative off-diagonal elements in the matrix are set to zero, and the diagonal is renormalized
            in order to assure that the returned matrix is a Markov transition matrix. 
            
            Returns a dictionary containing the volumes of the subvolumes, the system diffusion matrix
            and the fraction of the mass of the negative off-diagonal elements that has been filtered out.
            
        """
        
        # Check if the individual stiffness and mass matrices (per species) have been assembled, otherwise assemble them.
        try:
            stiffness_matrices = self.stiffness_matrices
            mass_matrices = self.mass_matrices
        except:
            matrices = assemble(self)
            self.stiffness_matrices = matrices['K']
            self.mass_matrices = matrices['M']
            stiffness_matrices = self.stiffness_matrices
            mass_matrices = self.mass_matrices
        
        # Make a dok matrix for easier manipulation
        i=1;
        Mspecies = len(self.listOfSpecies)
        Ndofs = self.mesh.getNumVoxels()*Mspecies
        S = scipy.sparse.dok_matrix((Ndofs,Ndofs))

        # Create the volume vector by lumping the mass matrices
        vol = numpy.zeros((Ndofs,1))
        spec = 0
        
        for species,M in mass_matrices.iteritems():
            rows,cols,vals = M.data()
            SM = scipy.sparse.csr_matrix((vals,cols,rows))
            vols = SM.sum(axis=1)
            spec = self.species_map[species]
            for j in range(len(vols)):
                vol[Mspecies*j+spec,0]=vols[j]

        # This is necessary in order for the array to have the right dimension (Ndofs,1) 
        vol = vol.flatten()
    
        # Assemble one big matrix from the indiviudal stiffness matrices. Multiply by the inverse of
        # the lumped mass matrix, filter out any entries with the wrong sign and renormalize the columns.
        spec = 0
        positive_mass = 0.0
        total_mass = 0.0
                
        for species,K in stiffness_matrices.iteritems():

            rows,cols,vals = K.data()
            Kcrs = scipy.sparse.csr_matrix((vals,cols,rows))
            Kdok = Kcrs.todok()
            
            for entries in Kdok.items():
                ind = entries[0]
                val = entries[1]
                
                if ind[0] != ind[1]:
                    if val > 0.0:
                        positive_mass += val
                        val = 0.0
                    else:
                        total_mass += val
                        
                # The volume can be zero, if the species is not active at the vertex (such as a 2D species at a 3D node)
                if vol[Mspecies*ind[1]+spec]==0:
                    vi = 1
                else:
                    vi = vol[Mspecies*ind[1]+spec]
            
                S[Mspecies*ind[0]+spec,Mspecies*ind[1]+spec]=-val/vi
            
            spec = spec+1

        # Convert to compressed column for compatibility with the URDME solvers.
        D = S.tocsc()
                
        # Renormalize the columns (may not sum to zero since elements may have been filtered out
        sumcol = numpy.zeros((Ndofs,1))
        for i in range(Ndofs):
           col = D.getcol(i)
           for val in col.data:
               if val > 0.0:
                   sumcol[i] += val
                
        D.setdiag(-sumcol.flatten())

        #print "Fraction of positive off-diagonal entries: " + str(numpy.abs(positive_mass/total_mass))
        return {'vol':vol,'D':D,'relative_positive_mass':positive_mass/total_mass}

                
    def validate(self):
        """ Validate the model data structures. 
            
            validate should be called prior to writing the model to the solver input file,
            since the solvers themselves do very limited error checking of the input.
        
        """
    
        # Check that all the columns of the system matrix sums to zero (or close to zero). If not, it does
        # not define a Markov process and the solvers might segfault or produce erraneous results.
        maxcolsum = numpy.max(numpy.abs(self.urdme_solver_data['D'].sum(axis=0)))
        if maxcolsum > 1e-10:
            raise InvalidSystemMatrixException("Invalid diffusion matrix. The sum of the columns does not sum to zero. " + str(maxcolsum))
                

    def initialize(self):
        """ Create the datastructures needed by the URDME solvers. 
            
            'initialize' creates and populates a dictionary, urdme_solver_data.
            All items of this dictionary will be dumped
            to the urdme input file upon invoking 'serialize'.  
            
        """
        
        if not self.urdme_solver_data['initialized']:
        
            
            # Stoichimetric matrix
            N = self.createStoichiometricMatrix()
            self.urdme_solver_data['N'] = N
            # Dependency Graph
            G = self.createDependencyGraph()
            self.urdme_solver_data['G']  = G
            
            # Volume vector
            result =  self.createSystemMatrix()
            vol = result['vol']
            #TODO: Make use of all dofs values, requires modification of CORE URDME...
            vol = vol[1::len(self.listOfSpecies)]
            
            self.urdme_solver_data['vol'] = vol
            D = result['D']
            self.urdme_solver_data['D'] = D
            
            # Subdomain vector
            if not "sd" in self.urdme_solver_data:
                self.urdme_solver_data['sd'] = self.initializeSubdomainVector()
                        
            # Data vector. If not present in model, it defaults to a vector with all elements zero.
            if not "data" in self.urdme_solver_data:
                data = np.zeros((1,self.mesh.getNumVoxels()))
                self.urdme_solver_data['data'] = data
    
            if not hasattr(self,'u0'):
                self.initializeInitialValue()
                    
            self.urdme_solver_data['u0'] = self.u0

            tspan= np.asarray(self.tspan,dtype=np.float)
            self.urdme_solver_data['tspan'] = tspan
        
            # Vertex coordinates
            self.urdme_solver_data['p'] = self.mesh.getVoxels()
        
            # Connectivity matrix
            self.urdme_solver_data['K'] = connectivityMatrix(self)

            self.urdme_solver_data['initialized'] = True

    def isInitialized(self):
        """ Determine if the model has been initialized with all the datastrucures. """
        try:
           # This will fail if the 'urdme_solver_data' dictionary has not been defined.
           isinit = self.urdme_solver_data['initialized']
           return isinit
        except:
            return False

    def serialize(self,filename=None):
        """ 
            Write the datastructures needed by the the core URDME solvers to a .mat input file.
            initialize() must be called prior to calling this function. 
        """
                
        # Validate the data structures before writing them to file. 
        self.validate()
        spio.savemat(filename,self.urdme_solver_data,oned_as='column')


class Mesh():
    """ A thin wrapper around the Dolfin mesh object.
            
        We wrap around dolfin mesh in order to present one
        unified API (pyurdme) to the user.
        
    """

    def __init__(self,mesh=None,mesh_type="Dolfin"):
        
        self.mesh_type = mesh_type
        
        if mesh_type == "Dolfin":
            self.mesh = mesh
        elif mesh_type == "Cartesian":
            return

    def getNumVoxels(self):
        return self.mesh.num_vertices()
    
    def getVoxels(self):
        return self.mesh.coordinates()

"""  Wrappers around dolfins built-in simple geometries/meshes.
    
    These following methods will all give regular meshes that will produce discretizations that are
    equivalent to Cartesian grids.

"""

def unitIntervalMesh(nx):
    mesh = dolfin.IntervalMesh(nx,0,1)
    return Mesh(mesh=mesh)

def IntervalMesh(nx,a,b):
    mesh = dolfin.IntervalMesh(nx,a,b)
    return Mesh(mesh=mesh)

def unitSquareMesh(nx,ny):
    """ Unit Square of with nx,ny points in the respective axes. """
    mesh = dolfin.UnitSquareMesh(nx,ny)
    print mesh.coordinates
    return Mesh(mesh=mesh)

def SquareMesh(L,nx,ny):
    """ Regular mesh of a square with side length L. """
    mesh = dolfin.RectangleMesh(0,0,L,L,nx,ny)
    return Mesh(mesh=mesh)
    
def unitCubeMesh(nx,ny,nz):
    """ Unit Square of with nx,ny points in the respective axes. """
    mesh = dolfin.UnitCubeMesh(nx,ny,nz)
    return Mesh(mesh=mesh)

#def unitCircle(nx,ny):
#    """ Unit Square of with nx,ny points in the respective axes. """
#    mesh = dolfin.UnitCircleMesh(nx,ny)
#    return Mesh(mesh=mesh)

#def unitSphere(nx,ny):
#    """ Unit Square of with nx,ny points in the respective axes. """
#    mesh = dolfin.UnitSquareMesh(nx,ny)
#    return Mesh(mesh=mesh)



def read_gmsh_mesh(meshfile):
    
    """ Read a Gmsh mesh from file. """
    mr = GmshMeshReceiverBase()
    try:
        mesh = read_gmsh(mr,filename=meshfile)
    except:
        raise MeshImportError("Failed to import mesh: "+filename)

    return mesh

def read_dolfin_mesh(filename=None):
    """ Import a mesh in Dolfins native .xml format """
    
    try:
        dolfin_mesh = dolfin.Mesh(filename)
        mesh = Mesh(mesh=dolfin_mesh,mesh_type="Dolfin")
        return mesh
    except Exception,e:
        raise MeshImportError("Failed to import mesh: "+filename+"\n"+e)


def connectivityMatrix(model):
    """ Assemble a connectivity matrix in CCS format. """

    fs = dolfin.FunctionSpace(model.mesh.mesh,"Lagrange",1)
    trial_function = dolfin.TrialFunction(fs)
    test_function = dolfin.TestFunction(fs)
    a_K = -1*dolfin.inner(dolfin.nabla_grad(trial_function), dolfin.nabla_grad(test_function))*dolfin.dx
    C = dolfin.assemble(a_K)
    rows,cols,vals = C.data()
    C = scipy.sparse.csr_matrix((vals,cols,rows))
    C = C.tocsc()

    return C

def assemble(model):
    """  Assemble the mass and stiffness matrices using Dolfin.
    
         Returns: A dictionary containing two dictionaries, one for the stiffness matrices
                  and one for the mass matrices. Those dictionaries has the species names as keys and
                  the matrices are in CSR format.
    """
    
    
    model.meshextend()
    
    function_space = model.xmesh.function_space
    #function_space = OrderedDict()
    trial_functions = OrderedDict()
    test_functions = OrderedDict()
    stiffness_matrices = OrderedDict()
    mass_matrices = OrderedDict()
    
    for spec in model.listOfSpecies:
        
        species = model.listOfSpecies[spec]
        spec_name = species.name
        
        if species.dimension == 2:
            # TODO: If the dimension of the mesh is 2 (triangles) and one uses ds,
            # The the mass matrices become strange...
            differential = dolfin.dx
        else:
            differential = dolfin.dx
    
        trial_functions[spec_name] = dolfin.TrialFunction(function_space[spec_name])
        test_functions[spec_name] = dolfin.TestFunction(function_space[spec_name])
        # We cannot include the diffusion constant in the assembly, dolfin does not seem to deal well with small diffusion consants (drops small elements)
        a_K = dolfin.inner(dolfin.nabla_grad(trial_functions[spec_name]), dolfin.nabla_grad(test_functions[spec_name]))*differential
        stiffness_matrices[spec_name] = dolfin.assemble(a_K)
        # Scale with the diffusion constant here.
        stiffness_matrices[spec_name] = species.diffusion_constant*stiffness_matrices[spec_name]
        a_M = trial_functions[spec_name]*test_functions[spec_name]*differential
        mass_matrices[spec_name] = dolfin.assemble(a_M)
    
    
    return {'K':stiffness_matrices,'M':mass_matrices}

class Xmesh():
    """ Extended mesh object.
        
        Contains function spaces and dof mappings.
    """

    def __init__(self):
        self.coordinates = None
        self.function_space = {}
        self.vertex_to_dof_map = {}
       


def toXYZ(model,filename,format="ParaView"):
    """ Dump the solution attached to a model as a xyz file. This format can be
        read by e.g. VMD, Jmol and Paraview. """
    
    
    if 'U' not in model.__dict__:
        print "No solution found in the model."
        raise

    #outfile = open(filename,"w")
    dims = numpy.shape(model.U)
    Ndofs = dims[0]
    Mspecies = len(model.listOfSpecies)
    Ncells = Ndofs/Mspecies

    coordinates = model.mesh.getVoxels()
    coordinatestr = coordinates.astype(str)

    if format == "VMD":
        outfile = open(filename,"w")
        filestr = ""
        for i,time in enumerate(model.tspan):
            number_of_atoms = numpy.sum(model.U[:,i])
            filestr += (str(number_of_atoms)+"\n"+"timestep "+str(i) + " time "+str(time)+"\n")
            for j,spec in enumerate(model.listOfSpecies):
                for k in range(Ncells):
                    for mol in range(model.U[k*Mspecies+j,i]):
                        linestr = spec + "\t" + '\t'.join(coordinatestr[k,:]) +"\n"
                        filestr += linestr

        outfile.write(filestr)
        outfile.close()

    elif format == "ParaView":
        foldername = filename
        subprocess.call(["mkdir",foldername])
        for i,time in enumerate(model.tspan):
            outfile = open(foldername+"/"+filename+"."+str(i),"w")
            number_of_atoms = numpy.sum(model.U[:,i])
            filestr = ""
            filestr += (str(number_of_atoms)+"\n"+"timestep "+str(i) + " time "+str(time)+"\n")
            for j,spec in enumerate(model.listOfSpecies):
                for k in range(Ncells):
                    for mol in range(model.U[k*Mspecies+j,i]):
                        linestr = spec + "\t" + '\t'.join(coordinatestr[k,:]) +"\n"
                        filestr += linestr
            outfile.write(filestr)
            outfile.close()

def toCSV(model,filename):
    """ Dump the solution attached to a model as a .csv file. """
    #TODO: Make this work for 2D meshes with only two coordinates.
    
    if 'U' not in model.__dict__:
        print "No solution found in the model."
        raise
    
    dims = numpy.shape(model.U)
    Ndofs = dims[0]
    Mspecies = len(model.listOfSpecies)
    Ncells = Ndofs/Mspecies
    
    coordinates = model.mesh.getVoxels()
    coordinatestr = coordinates.astype(str)
    subprocess.call(["mkdir",filename])
    for i,time in enumerate(model.tspan):
        outfile = open(filename+'/'+filename+str(i)+".csv","w")
        number_of_atoms = numpy.sum(model.U[:,i])
        filestr = "xcoord,ycoord,zcoord,radius,type\n"
        for j,spec in enumerate(model.listOfSpecies):
            for k in range(Ncells):
                for mol in range(model.U[k*Mspecies+j,i]):
                    obj = model.listOfSpecies[spec]
                    reaction_radius = obj.reaction_radius
                    linestr = coordinatestr[k,0]+","+coordinatestr[k,1]+","+coordinatestr[k,2]+","+str(reaction_radius)+","+str(j)+"\n";
                    filestr += linestr
        outfile.write(filestr)
        outfile.close()


def read_solution(filename):

    resultfile = h5py.File(filename,'r')

    U = resultfile['U']
    U = numpy.array(U)
    # This little hack makes U have the same structure as in the Matlab interface...
    dims = numpy.shape(U)
    U = U.reshape((dims[1],dims[0]))
    U = U.transpose()
    
    tspan = resultfile['tspan']
    tspan = numpy.array(tspan).flatten()
    resultfile.close()

    return {'U':U, 'tspan':tspan}


def urdme(model=None,solver='nsm',solver_path="", model_file=None, input_file=None, seed=None,report_level=1):
    """ URDME solver interface, analogous to the Matlab URDME interface. 
            
        TODO: Docs...
    
    """

    # Set URDME_ROOT. This requires that URDME is properly installed on the system.
    try:
        URDME_ROOT = subprocess.check_output(['urdme_init','-r'])
    except Exception,e:
        print "Could not determine the location of URDME."
        raise
    
    # Trim newline
    URDME_ROOT = URDME_ROOT[:-1]
    if solver_path == "":
        URDME_BUILD = URDME_ROOT+'/build/'
    else:
        URDME_BUILD = solver_path+'/build/'
        os.environ['SOLVER_ROOT'] = solver_path

    # Write the propensity file
    if os.path.isdir('.urdme'):
        shutil.rmtree('.urdme')

    try:
      os.mkdir('.urdme')
    except:
      pass
    
    propfilename= model.name+'_pyurdme_generated_model'
    if model_file == None:
        propfilename= model.name+'_pyurdme_generated_model'
        if solver != "nem":
            model.createPropensityFile(file_name='.urdme/'+propfilename+'.c')
        else:
            model.createNewPropensityFile(file_name='.urdme/'+propfilename+'.c')
    else:
        subprocess.call(['cp',model_file,'.urdme/'+propfilename+'.c'])

    # Build the solver
    makefile = 'Makefile.' + solver
    handle = subprocess.Popen(['make','-f',URDME_BUILD+makefile,'URDME_ROOT='+URDME_ROOT,'URDME_MODEL='+propfilename], stdout = subprocess.PIPE, stderr=subprocess.PIPE)
    handle.wait()

    if report_level >=1:
      print handle.stdout.read()
      print handle.stderr.read()

    if input_file is None:
        # Get temporary input and output files
        infile = tempfile.NamedTemporaryFile(delete=False)

        # Check that the model is initialized
        if not model.isInitialized():
           model.initialize()

        model.serialize(filename=infile)
        infile.close()
        infile_name = infile.name
    else:
        infile_name = input_file


    outfile = tempfile.NamedTemporaryFile(delete=False)
    outfile.close()

    # Execute the solver
    if seed is not None:
     try: 
      handle = subprocess.Popen(['.urdme/'+propfilename+'.'+solver,infile_name,outfile.name,str(seed)], stdout = subprocess.PIPE, stderr=subprocess.PIPE)
      handle.wait()
      if report_level >= 1:
        print handle.stdout.read()
        print handle.stderr.read()
     except:
        return {"status":"Failed","stderr":handle.stderr.read(),"stdout":handle.stdout.read()}
    else:
      try:
        handle = subprocess.Popen(['.urdme/'+propfilename+'.'+solver,infile_name,outfile.name], stdout = subprocess.PIPE, stderr=subprocess.PIPE)
        handle.wait()
        if report_level >= 1:
          print handle.stdout.read()
          print handle.stderr.read()
      except:
        return {"status":"Failed","stderr":handle.stderr.read(),"stdout":handle.stdout.read()}

    if input_file is None:
        subprocess.call(['cp',infile.name,'./debug_input.mat'])
    subprocess.call(['cp',outfile.name,'./debug_output.mat'])

    #Load the result from the hdf5 output file.
    try:
        
        result = read_solution(outfile.name)
        U = result['U']
        tspan = result['tspan']
        model.U = U
        
        # Create Dolfin Functions for all the species
        model.sol = {}
        # TODO: Create a dict of dolfin Functions, one for each species, indexed by tspan
        for i,spec in enumerate(model.listOfSpecies):
    
            species = model.listOfSpecies[spec]
            spec_name = species.name
            func = dolfin.Function(dolfin.FunctionSpace(model.mesh.mesh,"Lagrange",1))
            func_vector = func.vector()
            dims = U.shape
            
            numvox = model.mesh.getNumVoxels()
            for dof in range(numvox):
                func_vector[dof] = float(U[dof*len(model.listOfSpecies)+i,-1])
        
            model.sol[spec_name] = func

        # Clean up
        if input_file is None:
            os.remove(infile.name)
        os.remove(outfile.name)

        return dict({"Status":"Sucess","stdout":handle.stdout.read(),"stderr":handle.stderr.read()},**result)

    except Exception,e:
       # Clean up
       if input_file is None:
           subprocess.call(['rm','-rf',infile.name])
       subprocess.call(['rm','-rf',outfile.name])
       raise


class URDMEError(Exception):
    pass

if __name__ == '__main__':
    """ Command line interface to URDME. Execute URDME given a model file. """ 


class InvalidSystemMatrixException(Exception):
    pass



